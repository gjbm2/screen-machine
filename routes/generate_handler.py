"""
Generate handler - Utilities for generating content from other content
"""
import os
from pathlib import Path
import tempfile
import cv2
import logging
import json
import requests
import time
from PIL import Image
from io import BytesIO

from flask import send_file
from utils.logger import info, error, warning, debug
from routes.bucketer import _append_to_bucket

# Cache for GPU pricing to avoid repeated API calls
_gpu_price_cache = {}
_gpu_cache_expiry = 3600  # Cache for 1 hour (in seconds)

def calculate_generation_cost(seconds_taken, workflow_id=None, runpod_id=None, default_cost_per_second=0.000584):
    """
    Calculate the cost of image generation based on time taken and the GPU used by RunPod.
    
    Args:
        seconds_taken: Number of seconds the generation took
        workflow_id: Optional workflow ID to look up specific cost rate
        runpod_id: RunPod endpoint ID to get actual GPU costs from API
        default_cost_per_second: Default cost per second if API calls fail
        
    Returns:
        Cost in GBP
    """
    try:
        # Static exchange rate: 1 GBP = 1.3 USD
        FX_RATE_USD_TO_GBP = 1 / 1.3
        
        # Try to find the workflow's specific cost_per_second setting first
        cost_per_second_usd = default_cost_per_second
        used_real_time_pricing = False
        
        # Check cache first if runpod_id is provided
        cache_key = f"endpoint_{runpod_id}" if runpod_id else None
        current_time = time.time()
        
        if cache_key and cache_key in _gpu_price_cache:
            cache_entry = _gpu_price_cache[cache_key]
            # Check if cache is still valid
            if current_time - cache_entry['timestamp'] < _gpu_cache_expiry:
                info(f"Using cached GPU pricing for {cache_entry['gpu_name']}: ${cache_entry['cost_per_second']:.6f}/second")
                cost_per_second_usd = cache_entry['cost_per_second']
                used_real_time_pricing = True
            else:
                info(f"GPU price cache expired for {runpod_id}, refreshing...")
        
        # If not in cache or cache expired, and runpod_id is provided, try to get from API
        if not used_real_time_pricing and runpod_id:
            try:
                # Check if runpod_id is valid
                if not runpod_id or runpod_id == "None" or runpod_id.lower() == "null":
                    warning(f"Invalid RunPod ID: '{runpod_id}', falling back to default cost")
                else:
                    # Get RunPod API key from environment
                    api_key = os.getenv("RUNPOD_API_KEY")
                    if not api_key:
                        warning("RUNPOD_API_KEY not found in environment, falling back to default cost")
                    else:
                        # Correct GraphQL endpoint
                        headers = {"Authorization": f"Bearer {api_key}"}
                        gql_endpoint = "https://api.runpod.io/graphql"
                        
                        info(f"Fetching GPU info for RunPod endpoint ID: {runpod_id}")
                        
                        try:
                            # 1. Get GPU type for this endpoint using GraphQL
                            endpoint_query = """
                            query($id:String!){
                              myself { endpoints(filter:{id:$id}){ gpuIds name } }
                            }"""
                            
                            info(f"Calling RunPod GraphQL for endpoint info: {gql_endpoint}")
                            endpoint_response = requests.post(
                                gql_endpoint,
                                json={"query": endpoint_query, "variables": {"id": runpod_id}},
                                headers=headers,
                                timeout=5
                            )
                            
                            info(f"Endpoint query response status: {endpoint_response.status_code}")
                            
                            if endpoint_response.status_code != 200:
                                warning(f"Failed to get endpoint data: HTTP {endpoint_response.status_code}")
                                warning(f"Response content: {endpoint_response.text[:200]}")
                            else:
                                try:
                                    endpoint_data = endpoint_response.json()
                                    # Log a shortened version to avoid flooding logs
                                    response_excerpt = json.dumps(endpoint_data)[:200] + "..." if len(json.dumps(endpoint_data)) > 200 else json.dumps(endpoint_data)
                                    info(f"Endpoint query response: {response_excerpt}")
                                    
                                    if not endpoint_data.get("data") or not endpoint_data["data"].get("myself") or \
                                       not endpoint_data["data"]["myself"].get("endpoints") or \
                                       len(endpoint_data["data"]["myself"]["endpoints"]) == 0:
                                        warning(f"No endpoint data found in response: {response_excerpt}")
                                        if "errors" in endpoint_data:
                                            warning(f"GraphQL errors: {json.dumps(endpoint_data['errors'])}")
                                    else:
                                        # Extract GPU ID and endpoint name
                                        endpoint_info = endpoint_data["data"]["myself"]["endpoints"][0]
                                        gpu_id = endpoint_info.get("gpuIds")
                                        endpoint_name = endpoint_info.get("name", f"Endpoint {runpod_id}")
                                        
                                        if not gpu_id:
                                            warning(f"No GPU ID found for endpoint {runpod_id}")
                                        else:
                                            info(f"Found GPU ID: {gpu_id} for endpoint: {endpoint_name}")
                                            
                                            # 2. Get price for this GPU type using GraphQL
                                            price_query = """
                                            query($id:String!){
                                              gpuTypes(input:{id:$id}){ securePrice communityPrice displayName }
                                            }"""
                                            
                                            info(f"Calling RunPod GraphQL for GPU pricing: {gql_endpoint}")
                                            price_response = requests.post(
                                                gql_endpoint,
                                                json={"query": price_query, "variables": {"id": gpu_id}},
                                                headers=headers,
                                                timeout=5
                                            )
                                            
                                            info(f"Price query response status: {price_response.status_code}")
                                            
                                            if price_response.status_code != 200:
                                                warning(f"Failed to get GPU price data: HTTP {price_response.status_code}")
                                                warning(f"Response content: {price_response.text[:200]}")
                                            else:
                                                try:
                                                    price_data = price_response.json()
                                                    # Log a shortened version to avoid flooding logs
                                                    response_excerpt = json.dumps(price_data)[:200] + "..." if len(json.dumps(price_data)) > 200 else json.dumps(price_data)
                                                    info(f"Price query response: {response_excerpt}")
                                                    
                                                    if not price_data.get("data") or not price_data["data"].get("gpuTypes") or \
                                                       len(price_data["data"]["gpuTypes"]) == 0:
                                                        warning(f"No GPU type data found in response: {response_excerpt}")
                                                        if "errors" in price_data:
                                                            warning(f"GraphQL errors: {json.dumps(price_data['errors'])}")
                                                    else:
                                                        # Extract price and GPU display name
                                                        gpu_data = price_data["data"]["gpuTypes"][0]
                                                        price_per_hour = gpu_data.get("securePrice") or gpu_data.get("communityPrice", 0)
                                                        gpu_display_name = gpu_data.get("displayName", gpu_id)
                                                        
                                                        if price_per_hour == 0:
                                                            warning(f"Price per hour is zero for GPU {gpu_id}")
                                                        
                                                        # Convert to price per second (USD)
                                                        cost_per_second_usd = price_per_hour / 3600
                                                        
                                                        # Save to cache
                                                        if cache_key:
                                                            _gpu_price_cache[cache_key] = {
                                                                'cost_per_second': cost_per_second_usd,
                                                                'gpu_name': gpu_display_name,
                                                                'timestamp': current_time
                                                            }
                                                        
                                                        info(f"Using real-time pricing for {gpu_display_name}: ${cost_per_second_usd:.6f}/second")
                                                        used_real_time_pricing = True
                                                except json.JSONDecodeError as jde:
                                                    warning(f"Failed to parse price query response: {jde}")
                                                    warning(f"Raw response: {price_response.text[:200]}")
                                except json.JSONDecodeError as jde:
                                    warning(f"Failed to parse endpoint query response: {jde}")
                                    warning(f"Raw response: {endpoint_response.text[:200]}")
                        except requests.exceptions.RequestException as re:
                            warning(f"RunPod GraphQL request failed: {type(re).__name__}: {str(re)}")
            except Exception as e:
                warning(f"Error getting GPU price from RunPod API: {type(e).__name__}: {str(e)}")
                import traceback
                warning(f"Traceback: {traceback.format_exc()}")
        
        # Check workflow config as fallback if we didn't get real-time pricing
        if not used_real_time_pricing and workflow_id:
            # Import locally to avoid circular imports
            from routes.utils import findfile, _load_json_once
            
            # Load the workflows config
            workflow_config = _load_json_once("workflow", "workflows.json")
            
            # Find the matching workflow
            for workflow in workflow_config:
                if workflow.get("id") == workflow_id:
                    # Use the workflow's cost_per_second if specified
                    workflow_cost = workflow.get("cost_per_second")
                    if workflow_cost:
                        cost_per_second_usd = workflow_cost
                        info(f"Using workflow-specific cost rate: ${cost_per_second_usd:.6f}/second")
                        break
        
        # Convert USD cost to GBP
        cost_per_second_gbp = cost_per_second_usd * FX_RATE_USD_TO_GBP
        
        # Calculate and return the final cost in GBP
        return round(seconds_taken * cost_per_second_gbp, 6)
    except Exception as e:
        error(f"Error calculating generation cost: {e}")
        # Return default calculation if there's an error
        return round(seconds_taken * default_cost_per_second * (1/1.3), 6)

def jpg_from_mp4_handler(mp4_path):
    """
    Extract the first frame from an MP4 file and return it as a JPG.
    
    Args:
        mp4_path: Path to the MP4 file
        
    Returns:
        Flask response with the JPG image
    """
    temp_path = None
    try:
        debug(f"Extracting first frame from: {mp4_path}")
        
        # Convert string path to Path object if needed
        if isinstance(mp4_path, str):
            mp4_path = Path(mp4_path)
            
        # Check if the file exists
        if not mp4_path.exists():
            error(f"MP4 file not found: {mp4_path}")
            return "File not found", 404, {"Content-Type": "text/plain"}
            
        # Open the video file
        video = cv2.VideoCapture(str(mp4_path))
        
        # Check if video opened successfully
        if not video.isOpened():
            error(f"Failed to open MP4 file: {mp4_path}")
            return "Failed to open video file", 500, {"Content-Type": "text/plain"}
        
        # Read the first frame
        success, frame = video.read()
        if not success:
            error(f"Failed to read frame from MP4: {mp4_path}")
            return "Failed to read frame from video", 500, {"Content-Type": "text/plain"}
        
        # Release the video file
        video.release()
        
        # No need to convert BGR to RGB since cv2.imwrite expects BGR
        # We'll just use the frame as is
        
        # Create a temporary file for the JPG
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
            temp_path = temp_file.name
            
        # Save the frame as a JPG
        cv2.imwrite(temp_path, frame)
        
        # Return the JPG file
        debug(f"Successfully extracted frame to: {temp_path}")
        return send_file(
            temp_path, 
            mimetype='image/jpeg', 
            as_attachment=False,
            # Add custom cleanup callback to delete the temp file 
            # after response is sent
            download_name=f"frame_{os.path.basename(mp4_path)}.jpg"
        )
        
    except Exception as e:
        # Clean up the temp file if there was an error
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass  # Ignore errors in cleanup
        error(f"Error extracting frame from MP4: {e}")
        return f"Error extracting frame: {e}", 500, {"Content-Type": "text/plain"}
    
    # The temp file cleanup is handled by the endpoint function 

def save_to_recent(img_url, batch_id, metadata=None):
    """
    Downloads the image from img_url, converts it to JPEG, and appends it to the _recent bucket.
    If *metadata* is provided, it is written to the side-car so that generation params are preserved.
    Returns the target path if successful, None otherwise.
    """
    try:
        response = requests.get(img_url)
        if response.status_code != 200:
            error(f"[save_to_recent] Failed to download image from {img_url}: {response.status_code}")
            return None
        info(f"[save_to_recent] Downloaded image from {img_url}, converting to JPEG")
        img = Image.open(BytesIO(response.content)).convert("RGB")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            img.save(temp_file, format="JPEG", quality=90)
            temp_file.flush()
            temp_path = Path(temp_file.name)
        info(f"[save_to_recent] Calling _append_to_bucket with batch_id={batch_id}")
        target_path = _append_to_bucket("_recent", temp_path, batch_id=batch_id, metadata=metadata)
        temp_path.unlink()
        if target_path:
            info(f"[save_to_recent] Successfully saved to _recent: {target_path}")
            return target_path
        else:
            error(f"[save_to_recent] _append_to_bucket returned None for {img_url}")
            return None
    except Exception as e:
        error(f"[save_to_recent] Failed to save image to _recent: {e}")
        import traceback
        error(f"[save_to_recent] Traceback: {traceback.format_exc()}")
        return None 