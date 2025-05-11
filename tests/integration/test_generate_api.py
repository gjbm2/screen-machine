import pytest
import os
import tempfile
from pathlib import Path
import shutil
import responses  # For mocking HTTP requests
import cv2
import numpy as np
from io import BytesIO
from PIL import Image

from app import app as flask_app

@pytest.fixture
def test_client():
    """Create a test client for the Flask app."""
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client

@pytest.fixture
def test_mp4_file():
    """Create a test MP4 file for testing."""
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    
    # Create a simple MP4 file
    test_mp4_path = os.path.join(temp_dir, "test_video.mp4")
    
    # Create a simple video with OpenCV
    # Using a 10x10 black frame for simplicity
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(test_mp4_path, fourcc, 25.0, (10, 10))
    
    # Create a red frame
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    frame[:, :, 2] = 255  # Red in BGR
    
    # Write the frame multiple times
    for _ in range(10):
        out.write(frame)
    
    out.release()
    
    yield test_mp4_path
    
    # Clean up
    shutil.rmtree(temp_dir)

@pytest.fixture
def setup_output_dir(test_mp4_file):
    """Set up a mock output directory with a test MP4 file."""
    # Create a temporary directory to simulate the output directory
    temp_output_dir = tempfile.mkdtemp()
    os.makedirs(os.path.join(temp_output_dir, "bucket_a"), exist_ok=True)
    
    # Copy the test MP4 file to the output directory
    output_mp4_path = os.path.join(temp_output_dir, "test_output.mp4")
    shutil.copy(test_mp4_file, output_mp4_path)
    
    # Also copy to simulate a bucket location
    bucket_mp4_path = os.path.join(temp_output_dir, "bucket_a", "bucket_video.mp4")
    shutil.copy(test_mp4_file, bucket_mp4_path)
    
    # Override the OUTPUT_DIR config
    original_output_dir = flask_app.config.get('OUTPUT_DIR')
    flask_app.config['OUTPUT_DIR'] = temp_output_dir
    
    yield {
        "output_dir": temp_output_dir,
        "output_mp4_path": output_mp4_path,
        "bucket_mp4_path": bucket_mp4_path,
        "relative_mp4_path": "test_output.mp4",
        "relative_bucket_mp4_path": "bucket_a/bucket_video.mp4"
    }
    
    # Clean up and restore config
    flask_app.config['OUTPUT_DIR'] = original_output_dir
    shutil.rmtree(temp_output_dir)

def is_valid_jpeg(data):
    """Check if the data is a valid JPEG image."""
    try:
        Image.open(BytesIO(data))
        return True
    except:
        return False

def test_jpg_from_mp4_local_path(test_client, setup_output_dir):
    """Test the jpg_from_mp4 endpoint with a local file path."""
    # Call the endpoint with a local file path
    response = test_client.get(f"/api/generate/jpg_from_mp4?file={setup_output_dir['output_mp4_path']}")
    
    # Assert successful response
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    assert is_valid_jpeg(response.data)

def test_jpg_from_mp4_output_relative_path(test_client, setup_output_dir):
    """Test the jpg_from_mp4 endpoint with a path relative to /output/."""
    # Call the endpoint with a path that starts with /output/
    response = test_client.get(f"/api/generate/jpg_from_mp4?file=/output/{setup_output_dir['relative_mp4_path']}")
    
    # Assert successful response
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    assert is_valid_jpeg(response.data)

def test_jpg_from_mp4_output_bucket_path(test_client, setup_output_dir):
    """Test the jpg_from_mp4 endpoint with a bucket path under /output/."""
    # Call the endpoint with a bucket path under /output/
    response = test_client.get(f"/api/generate/jpg_from_mp4?file=/output/{setup_output_dir['relative_bucket_mp4_path']}")
    
    # Assert successful response
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    assert is_valid_jpeg(response.data)

@responses.activate
def test_jpg_from_mp4_external_url(test_client, test_mp4_file):
    """Test the jpg_from_mp4 endpoint with an external URL."""
    # Mock the external URL response
    with open(test_mp4_file, 'rb') as f:
        mp4_data = f.read()
    
    # Set up a mock response for an external URL
    test_url = "http://example.com/test_video.mp4"
    responses.add(
        responses.GET,
        test_url,
        body=mp4_data,
        status=200,
        content_type="video/mp4"
    )
    
    # Call the endpoint with the external URL
    response = test_client.get(f"/api/generate/jpg_from_mp4?file={test_url}")
    
    # Assert successful response
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    assert is_valid_jpeg(response.data)

def test_jpg_from_mp4_nonexistent_file(test_client):
    """Test the jpg_from_mp4 endpoint with a nonexistent file."""
    # Call the endpoint with a nonexistent file
    response = test_client.get("/api/generate/jpg_from_mp4?file=/nonexistent/path/video.mp4")
    
    # Assert error response
    assert response.status_code == 404

def test_jpg_from_mp4_invalid_file_type(test_client, setup_output_dir):
    """Test the jpg_from_mp4 endpoint with an invalid file type."""
    # Create a text file with an mp4 extension
    fake_mp4_path = os.path.join(setup_output_dir["output_dir"], "fake.mp4")
    with open(fake_mp4_path, 'w') as f:
        f.write("This is not an MP4 file")
    
    # Call the endpoint with the invalid file
    response = test_client.get(f"/api/generate/jpg_from_mp4?file={fake_mp4_path}")
    
    # Assert error response - this might be 400 or 500 depending on how your handler validates
    assert response.status_code in (400, 500)

def test_jpg_from_mp4_missing_file_parameter(test_client):
    """Test the jpg_from_mp4 endpoint with a missing file parameter."""
    # Call the endpoint without the file parameter
    response = test_client.get("/api/generate/jpg_from_mp4")
    
    # Assert error response
    assert response.status_code == 400
    
def test_debug_output_dir(test_client, setup_output_dir):
    """Debug test to print the OUTPUT_DIR configuration."""
    # This test helps us debug by printing the current OUTPUT_DIR config
    print(f"Current OUTPUT_DIR: {flask_app.config.get('OUTPUT_DIR')}")
    print(f"Test output directory: {setup_output_dir['output_dir']}")
    print(f"File exists: {os.path.exists(setup_output_dir['output_mp4_path'])}")
    assert True  # Always pass 