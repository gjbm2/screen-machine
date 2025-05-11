import pytest
import os
import tempfile
import cv2
import numpy as np
import requests
from io import BytesIO
from pathlib import Path
from PIL import Image
import urllib.parse

# Define test constants
TEST_SERVER = "http://127.0.0.1:5000"  # Adjust if your server runs on a different URL
ENDPOINT = "/api/generate/jpg_from_mp4"
EXTERNAL_MP4_URL = "http://185.254.136.253:8000/output/devtest.mp4"

@pytest.fixture
def test_mp4():
    """Create a temporary test MP4 file and return its path."""
    # Create a temp file
    fd, mp4_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    
    # Create a simple video with a red frame
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(mp4_path, fourcc, 25.0, (320, 240))
    
    # Create a red frame
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :, 2] = 255  # Red in BGR
    
    # Write a few frames
    for _ in range(5):
        out.write(frame)
    
    out.release()
    
    # Return path as string
    yield mp4_path
    
    # Cleanup
    if os.path.exists(mp4_path):
        os.unlink(mp4_path)

class TestJpgFromMp4:
    """Test suite for jpg_from_mp4 endpoint."""
    
    def is_valid_jpeg(self, data):
        """Check if data is a valid JPEG image."""
        try:
            img = Image.open(BytesIO(data))
            return img.format == 'JPEG'
        except Exception:
            return False
    
    def test_local_file_path(self, test_mp4):
        """Test the endpoint with a local file path."""
        response = requests.get(
            f"{TEST_SERVER}{ENDPOINT}", 
            params={"file": test_mp4}
        )
        
        assert response.status_code == 200
        assert response.headers.get('Content-Type', '').startswith('image/jpeg')
        assert self.is_valid_jpeg(response.content)
    
    def test_external_url(self):
        """Test the endpoint with an external URL."""
        response = requests.get(
            f"{TEST_SERVER}{ENDPOINT}", 
            params={"file": EXTERNAL_MP4_URL}
        )
        
        assert response.status_code == 200
        assert response.headers.get('Content-Type', '').startswith('image/jpeg')
        assert self.is_valid_jpeg(response.content)
    
    def test_output_path(self, test_mp4):
        """Test the endpoint with a path in /output/ format."""
        # Copy the test file to the output directory
        output_dir = os.path.join(os.getcwd(), "output")
        os.makedirs(output_dir, exist_ok=True)
        
        output_mp4 = os.path.join(output_dir, "test_output.mp4")
        with open(test_mp4, 'rb') as src, open(output_mp4, 'wb') as dst:
            dst.write(src.read())
        
        try:
            response = requests.get(
                f"{TEST_SERVER}{ENDPOINT}", 
                params={"file": "/output/test_output.mp4"}
            )
            
            assert response.status_code == 200
            assert response.headers.get('Content-Type', '').startswith('image/jpeg')
            assert self.is_valid_jpeg(response.content)
        finally:
            # Clean up
            if os.path.exists(output_mp4):
                os.unlink(output_mp4)
    
    def test_missing_file_param(self):
        """Test the endpoint with a missing file parameter."""
        response = requests.get(f"{TEST_SERVER}{ENDPOINT}")
        assert response.status_code == 400
    
    def test_nonexistent_file(self):
        """Test the endpoint with a nonexistent file."""
        response = requests.get(
            f"{TEST_SERVER}{ENDPOINT}", 
            params={"file": "/nonexistent/path.mp4"}
        )
        assert response.status_code == 404
    
    def test_url_parameter_encoding(self):
        """Test the endpoint with a URL parameter that needs encoding."""
        encoded_url = urllib.parse.quote_plus(EXTERNAL_MP4_URL)
        response = requests.get(
            f"{TEST_SERVER}{ENDPOINT}?file={encoded_url}"
        )
        
        assert response.status_code == 200
        assert response.headers.get('Content-Type', '').startswith('image/jpeg')
        assert self.is_valid_jpeg(response.content) 