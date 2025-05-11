import pytest
import requests
from io import BytesIO
from PIL import Image

# Test constants - use the actual backend URL
API_URL = "http://185.254.136.253:5000/api/generate/jpg_from_mp4"
LOCAL_MP4_PATH = "/output/devtest.mp4"
EXTERNAL_MP4_URL = "http://185.254.136.253:8000/output/devtest.mp4"

def is_valid_jpeg(data):
    """Check if data is a valid JPEG image."""
    try:
        img = Image.open(BytesIO(data))
        return img.format == 'JPEG'
    except Exception:
        return False

def test_jpg_from_mp4_with_local_path():
    """Test the jpg_from_mp4 endpoint with a local /output/ path."""
    response = requests.get(
        API_URL,
        params={"file": LOCAL_MP4_PATH}
    )
    
    assert response.status_code == 200, f"Status code: {response.status_code}, Response: {response.text[:100]}"
    assert response.headers.get('Content-Type', '').startswith('image/jpeg'), f"Content-Type: {response.headers.get('Content-Type')}"
    assert is_valid_jpeg(response.content), "Response is not a valid JPEG image"

def test_jpg_from_mp4_with_external_url():
    """Test the jpg_from_mp4 endpoint with an external URL."""
    response = requests.get(
        API_URL,
        params={"file": EXTERNAL_MP4_URL}
    )
    
    assert response.status_code == 200, f"Status code: {response.status_code}, Response: {response.text[:100]}"
    assert response.headers.get('Content-Type', '').startswith('image/jpeg'), f"Content-Type: {response.headers.get('Content-Type')}"
    assert is_valid_jpeg(response.content), "Response is not a valid JPEG image"

def test_jpg_from_mp4_missing_file_param():
    """Test the jpg_from_mp4 endpoint with a missing file parameter."""
    response = requests.get(API_URL)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}" 