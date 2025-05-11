#!/usr/bin/env python3
"""
Manual test script for the jpg_from_mp4 endpoint
"""

import requests
import os
import sys
from pathlib import Path
from PIL import Image
import io

def test_endpoint(base_url, test_file_or_url, save_path=None):
    """Test the jpg_from_mp4 endpoint with a file path or URL."""
    endpoint = f"{base_url}/api/generate/jpg_from_mp4"
    params = {"file": test_file_or_url}
    
    print(f"Testing endpoint: {endpoint}")
    print(f"With file parameter: {test_file_or_url}")
    
    try:
        # Send request to the API
        response = requests.get(endpoint, params=params, stream=True)
        
        # Check response
        print(f"Response status code: {response.status_code}")
        print(f"Response content type: {response.headers.get('Content-Type', 'unknown')}")
        
        if response.status_code == 200:
            if response.headers.get('Content-Type', '').startswith('image/'):
                # Try to open as image to validate
                try:
                    img = Image.open(io.BytesIO(response.content))
                    print(f"Successfully received image: {img.format}, {img.size}px")
                    
                    # Save the image if requested
                    if save_path:
                        with open(save_path, 'wb') as f:
                            f.write(response.content)
                        print(f"Saved image to: {save_path}")
                except Exception as e:
                    print(f"Error opening received data as image: {e}")
            else:
                print(f"Received non-image response: {response.text[:100]}...")
        else:
            print(f"Error response: {response.text}")
            
    except Exception as e:
        print(f"Request error: {e}")

def main():
    # Set up parameters
    base_url = "http://127.0.0.1:5000"  # Change this to your server URL
    
    # Create output directory for test results
    test_results_dir = Path("./test_results")
    test_results_dir.mkdir(exist_ok=True)
    
    # 1. Test with a local file path
    local_mp4 = Path("./output/test.mp4")
    if local_mp4.exists():
        test_endpoint(
            base_url, 
            str(local_mp4.absolute()),
            str(test_results_dir / "local_test.jpg")
        )
    else:
        print(f"Warning: Local test file not found at {local_mp4}")
        
    print("\n" + "-"*60 + "\n")
    
    # 2. Test with /output/ path
    test_endpoint(
        base_url, 
        "/output/test.mp4",
        str(test_results_dir / "output_test.jpg")
    )
    
    print("\n" + "-"*60 + "\n")
    
    # 3. Test with external URL
    test_endpoint(
        base_url, 
        "http://185.254.136.253:8000/output/devtest.mp4",
        str(test_results_dir / "url_test.jpg")
    )
    
    # Additional debugging test with your specific case
    print("\n" + "-"*60 + "\n")
    print("Debug - testing exact problematic URLs:")
    
    # The URLs that were failing
    debug_urls = [
        "http://185.254.136.253:5000/api/generate/jpg_from_mp4?file=http://185.254.136.253:8000/output/devtest.mp4",
        "http://185.254.136.253:5000/api/generate/jpg_from_mp4?file=/output/devtest.mp4"
    ]
    
    # Extract just the file parameters
    for url in debug_urls:
        file_param = url.split("?file=", 1)[1] if "?file=" in url else None
        if file_param:
            test_endpoint(
                base_url, 
                file_param,
                str(test_results_dir / f"debug_{Path(file_param).name}.jpg")
            )
            print("\n" + "-"*30 + "\n")

if __name__ == "__main__":
    main() 