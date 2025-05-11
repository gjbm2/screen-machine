import pytest
import json
import os
import shutil
from pathlib import Path
import tempfile

from routes.publisher import _record_publish
from routes.bucketer import load_meta, bucket_path

def test_cross_bucket_mp4_url_format():
    """Test that cross-bucket MP4 publishing correctly formats the URLs."""
    # Test bucket
    bucket_id = "testbucket"
    bucket_dir = bucket_path(bucket_id)
    
    # Create test bucket if it doesn't exist
    bucket_dir.mkdir(exist_ok=True, parents=True)
    
    # Create a test MP4 filename
    test_filename = "test-video.mp4"
    
    # Test timestamp
    test_timestamp = "2025-05-11T15:05:00.498026Z"
    
    # Get API URL for testing
    vite_api_url = os.environ.get("VITE_API_URL", "http://185.254.136.253:5000/api").rstrip("/")
    
    # Test with cross_bucket_mode=True and MP4 file extension
    _record_publish(
        bucket=bucket_id,
        filename=test_filename,
        when=test_timestamp,
        source_metadata={"type": "video"},
        cross_bucket_mode=True,
        file_extension=".mp4"
    )
    
    # Load the bucket metadata to verify URL formats
    meta = load_meta(bucket_id)
    published_meta = meta.get("published_meta", {})
    
    # Check that raw_url uses the proper format for cross-bucket MP4
    assert published_meta.get("raw_url") == f"/output/{bucket_id}.mp4", \
        f"Expected MP4 raw_url to be '/output/{bucket_id}.mp4', got '{published_meta.get('raw_url')}'"
    
    # Check that thumbnail_url uses the jpg_from_mp4 endpoint with proper API URL
    expected_thumbnail_url = f"{vite_api_url}/generate/jpg_from_mp4?file=/output/{bucket_id}.mp4"
    
    assert published_meta.get("thumbnail_url") == expected_thumbnail_url, \
        f"Expected MP4 thumbnail_url to be '{expected_thumbnail_url}', got '{published_meta.get('thumbnail_url')}'"
    
    # Test with cross_bucket_mode=True and JPG file extension
    _record_publish(
        bucket=bucket_id,
        filename="test-image.jpg",
        when=test_timestamp,
        source_metadata={"type": "image"},
        cross_bucket_mode=True,
        file_extension=".jpg"
    )
    
    # Load the bucket metadata to verify URL formats for JPG
    meta = load_meta(bucket_id)
    published_meta = meta.get("published_meta", {})
    
    # Check that raw_url uses the proper format for cross-bucket JPG
    assert published_meta.get("raw_url") == f"/output/{bucket_id}.jpg", \
        f"Expected JPG raw_url to be '/output/{bucket_id}.jpg', got '{published_meta.get('raw_url')}'"
    
    # Check that thumbnail_url is the same as raw_url for images
    assert published_meta.get("thumbnail_url") == published_meta.get("raw_url"), \
        f"Expected JPG thumbnail_url to be the same as raw_url, got '{published_meta.get('thumbnail_url')}'" 