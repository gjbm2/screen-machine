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

from flask import send_file
from utils.logger import info, error, warning, debug

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
        
        # Check cache first if runpod_id is provided
        cache_key = f"endpoint_{runpod_id}" if runpod_id else None
        current_time = time.time()
        
        if cache_key and cache_key in _gpu_price_cache:
            cache_entry = _gpu_price_cache[cache_key]
            # Check if cache is still valid
            if current_time - cache_entry['timestamp'] < _gpu_cache_expiry:
                info(f"Using cached GPU pricing for {cache_entry['gpu_name']}: ${cache_entry['cost_per_second']:.6f}/second")
                cost_per_second_usd = cache_entry['cost_per_second']
            else:
                info(f"GPU price cache expired for {runpod_id}, refreshing...")
        
        # If not in cache or cache expired, and runpod_id is provided, try to get from API
        if cost_per_second_usd == default_cost_per_second and runpod_id:
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
                        # Setup API endpoints and headers
                        api_base = "https://api.runpod.ai"
                        gql_endpoint = f"{api_base}/graphql"
                        headers = {"Authorization": f"Bearer {api_key}"}
                        
                        info(f"Fetching GPU info for RunPod endpoint ID: {runpod_id}")
                        
                        # 1. Get GPU ID from the health endpoint
                        health_endpoint = f"{api_base}/v2/{runpod_id}/health"
                        info(f"Calling RunPod health endpoint: {health_endpoint}")
                        
                        try:
                            health_response = requests.get(
                                health_endpoint, 
                                headers=headers,
                                timeout=5  # 5 second timeout
                            )
                            
                            info(f"Health endpoint response status: {health_response.status_code}")
                            
                            if health_response.status_code == 401:
                                warning("RunPod API authentication failed - invalid API key")
                            elif health_response.status_code == 403:
                                warning("RunPod API access forbidden - insufficient permissions")
                            elif health_response.status_code == 404:
                                warning(f"Endpoint ID not found: {runpod_id}")
                            elif health_response.status_code == 200:
                                try:
                                    health_data = health_response.json()
                                    # Log a shortened version of the response to avoid flooding logs
                                    response_excerpt = json.dumps(health_data)[:200] + "..." if len(json.dumps(health_data)) > 200 else json.dumps(health_data)
                                    info(f"Health endpoint response: {response_excerpt}")
                                    
                                    # The health endpoint has a different format than expected - it returns worker counts 
                                    # instead of individual worker objects with GPU info
                                    if "workers" in health_data and isinstance(health_data["workers"], dict):
                                        info(f"Health endpoint returns worker counts, not worker details: {health_data['workers']}")
                                        info(f"Need to use different approach to get GPU info for endpoint {runpod_id}")
                                        
                                        # Alternative: get GPU info from endpoint metadata 
                                        # or directly from workflow config
                                        
                                        # Fall back to checking workflow config since worker-specific GPU info is not available
                                        from routes.utils import findfile, _load_json_once
                                        
                                        # Load the workflows config
                                        workflow_config = _load_json_once("workflow", "workflows.json")
                                        
                                        # Try to find a workflow that matches this runpod_id
                                        matching_workflow = None
                                        for workflow in workflow_config:
                                            if workflow.get("runpod_id") == runpod_id:
                                                matching_workflow = workflow
                                                break
                                        
                                        if matching_workflow:
                                            workflow_id = matching_workflow.get("id")
                                            workflow_cost = matching_workflow.get("cost_per_second")
                                            if workflow_cost:
                                                cost_per_second_usd = workflow_cost
                                                info(f"Using cost from workflow {workflow_id} for RunPod endpoint {runpod_id}: ${cost_per_second_usd:.6f}/second")
                                                
                                                # Save to cache
                                                if cache_key:
                                                    _gpu_price_cache[cache_key] = {
                                                        'cost_per_second': cost_per_second_usd,
                                                        'gpu_name': f"Endpoint {runpod_id}",
                                                        'timestamp': current_time
                                                    }
                                    # The original expected format - an array of worker objects
                                    elif "workers" in health_data and isinstance(health_data["workers"], list) and len(health_data["workers"]) > 0:
                                        worker = health_data["workers"][0]
                                        gpu_id = worker.get("gpuId")
                                        gpu_name = worker.get("gpuDisplayName", "Unknown GPU")
                                        
                                        if not gpu_id:
                                            warning("No GPU ID found in worker data")
                                        else:
                                            info(f"Found GPU: ID={gpu_id}, Name={gpu_name}")
                                            
                                            # 2. Get the price for this GPU type using GraphQL
                                            query = """query($id: String!){ gpuTypes(input:{id:$id}){securePrice communityPrice}}"""
                                            info(f"Calling RunPod GraphQL endpoint for GPU pricing: {gql_endpoint}")
                                            
                                            try:
                                                gql_response = requests.post(
                                                    gql_endpoint, 
                                                    json={"query": query, "variables": {"id": gpu_id}},
                                                    headers=headers,
                                                    timeout=5
                                                )
                                                
                                                info(f"GraphQL response status: {gql_response.status_code}")
                                                
                                                if gql_response.status_code != 200:
                                                    warning(f"Failed to get GPU price data: HTTP {gql_response.status_code}")
                                                    warning(f"Response content: {gql_response.text[:200]}")
                                                else:
                                                    try:
                                                        gql_data = gql_response.json()
                                                        # Log a shortened version to avoid flooding logs
                                                        response_excerpt = json.dumps(gql_data)[:200] + "..." if len(json.dumps(gql_data)) > 200 else json.dumps(gql_data)
                                                        info(f"GraphQL response: {response_excerpt}")
                                                        
                                                        if "data" not in gql_data or not gql_data.get("data"):
                                                            warning(f"No data found in GraphQL response: {response_excerpt}")
                                                            if "errors" in gql_data:
                                                                warning(f"GraphQL errors: {json.dumps(gql_data['errors'])}")
                                                        else:
                                                            gpu_types = gql_data.get("data", {}).get("gpuTypes", [])
                                                            if not gpu_types or len(gpu_types) == 0:
                                                                warning(f"No GPU types found in GraphQL response")
                                                            else:
                                                                gpu_data = gpu_types[0]
                                                                
                                                                # Get secure price (or community price as fallback)
                                                                price_per_hour = gpu_data.get("securePrice") or gpu_data.get("communityPrice", 0)
                                                                
                                                                if price_per_hour == 0:
                                                                    warning(f"Price per hour is zero for GPU {gpu_id}")
                                                                
                                                                # 3. Convert to price per second (USD)
                                                                cost_per_second_usd = price_per_hour / 3600
                                                                
                                                                # Save to cache
                                                                if cache_key:
                                                                    _gpu_price_cache[cache_key] = {
                                                                        'cost_per_second': cost_per_second_usd,
                                                                        'gpu_name': gpu_name,
                                                                        'timestamp': current_time
                                                                    }
                                                                
                                                                info(f"Using real-time pricing for {gpu_name}: ${cost_per_second_usd:.6f}/second")
                                                    except json.JSONDecodeError as jde:
                                                        warning(f"Failed to parse GraphQL response: {jde}")
                                                        warning(f"Raw response: {gql_response.text[:200]}")
                                            except requests.exceptions.RequestException as re:
                                                warning(f"GraphQL request failed: {type(re).__name__}: {str(re)}")
                                except json.JSONDecodeError as jde:
                                    warning(f"Failed to parse health response: {jde}")
                                    warning(f"Raw response: {health_response.text[:200]}")
                            else:
                                warning(f"Failed to get GPU info from RunPod API: HTTP {health_response.status_code}")
                                warning(f"Response content: {health_response.text[:200]}")
                        except requests.exceptions.RequestException as re:
                            warning(f"RunPod health endpoint request failed: {type(re).__name__}: {str(re)}")
            except Exception as e:
                warning(f"Error getting GPU price from RunPod API: {type(e).__name__}: {str(e)}")
                import traceback
                warning(f"Traceback: {traceback.format_exc()}")
        
        # Check workflow config as fallback
        if cost_per_second_usd == default_cost_per_second and workflow_id:
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