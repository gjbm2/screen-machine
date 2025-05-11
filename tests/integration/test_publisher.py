import pytest
import os
import json
import shutil
from pathlib import Path
import tempfile
from unittest.mock import patch, MagicMock

# Import the publisher module and related functions
from routes.publisher import (
    publish_to_destination,
    _publish_to_destination,
    _record_publish,
    get_published_info
)

from routes.bucketer import (
    bucket_path,
    meta_path,
    _append_to_bucket,
    load_meta,
    save_meta
)

# Constants for testing
TEST_JPG_FILENAME = "test_image.jpg"
TEST_MP4_FILENAME = "test_video.mp4"

@pytest.fixture
def mock_publish_destinations():
    """Mock publish destinations for testing."""
    return [
        {
            "id": "bucket_a",
            "name": "Bucket A",
            "description": "Test bucket A",
            "has_bucket": True,
            "file": "bucket_a",
            "headless": False
        },
        {
            "id": "bucket_b",
            "name": "Bucket B",
            "description": "Test bucket B",
            "has_bucket": True,
            "file": "bucket_b",
            "headless": False
        }
    ]

@pytest.fixture
def setup_test_output_directory(tmp_path):
    """Create a temporary output directory structure for testing."""
    # Create root output dir
    output_dir = tmp_path / "output"
    output_dir.mkdir(exist_ok=True)
    
    # Create bucket directories
    bucket_a_dir = output_dir / "bucket_a"
    bucket_b_dir = output_dir / "bucket_b"
    bucket_a_dir.mkdir(exist_ok=True)
    bucket_b_dir.mkdir(exist_ok=True)
    
    # Create thumbnails directories
    (bucket_a_dir / "thumbnails").mkdir(exist_ok=True)
    (bucket_b_dir / "thumbnails").mkdir(exist_ok=True)
    
    # Create empty bucket.json files to simulate existing buckets
    bucket_a_meta = {
        "sequence": [],
        "favorites": []
    }
    bucket_b_meta = {
        "sequence": [],
        "favorites": []
    }
    
    with open(bucket_a_dir / "bucket.json", "w") as f:
        json.dump(bucket_a_meta, f, indent=2)
    
    with open(bucket_b_dir / "bucket.json", "w") as f:
        json.dump(bucket_b_meta, f, indent=2)
    
    # Create test image and video files
    create_test_media_files(output_dir)
    
    return {
        "output_dir": output_dir,
        "bucket_a_dir": bucket_a_dir,
        "bucket_b_dir": bucket_b_dir
    }

def create_test_media_files(output_dir):
    """Create test media files for testing."""
    # Create a sample JPG file
    jpg_file = output_dir / TEST_JPG_FILENAME
    with open(jpg_file, "wb") as f:
        # Create a minimal valid JPG file
        f.write(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x43\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xff\xd9')
    
    # Create JPG sidecar file
    jpg_sidecar = output_dir / f"{TEST_JPG_FILENAME}.json"
    with open(jpg_sidecar, "w") as f:
        json.dump({
            "type": "image",
            "width": 1,
            "height": 1,
            "prompt": "Test JPG image"
        }, f, indent=2)
    
    # Create a sample MP4 file
    mp4_file = output_dir / TEST_MP4_FILENAME
    with open(mp4_file, "wb") as f:
        # Create a minimal MP4 file signature
        f.write(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41\x00\x00\x00\x00moov')
    
    # Create MP4 sidecar file
    mp4_sidecar = output_dir / f"{TEST_MP4_FILENAME}.json"
    with open(mp4_sidecar, "w") as f:
        json.dump({
            "type": "video",
            "width": 640,
            "height": 480,
            "prompt": "Test MP4 video",
            "fps": 30
        }, f, indent=2)

@pytest.fixture
def patch_publisher_functions(setup_test_output_directory, mock_publish_destinations, monkeypatch):
    """
    Patch publishing-related functions to use the test directory
    and mock destinations.
    """
    output_dir = setup_test_output_directory["output_dir"]
    
    # Mock the get_destination function to return our test destinations
    def mock_get_destination(dest_id):
        for dest in mock_publish_destinations:
            if dest["id"] == dest_id:
                return dest
        raise KeyError(f"Unknown destination: {dest_id}")
    
    # Patch bucket_path to use our test directory
    def mock_bucket_path(name):
        return output_dir / name
    
    # Patch ensure_sidecar_for to do nothing (we'll create sidecars manually)
    def mock_ensure_sidecar_for(path):
        return path
    
    # Patch sidecar_path to use our naming convention
    def mock_sidecar_path(filepath):
        if isinstance(filepath, str):
            filepath = Path(filepath)
        return filepath.parent / f"{filepath.name}.json"
    
    # Patch _load_publish_destinations to use our mock destinations
    def mock_load_publish_destinations():
        from routes.publisher import publish_destinations
        publish_destinations = mock_publish_destinations
    
    # Apply all patches
    monkeypatch.setattr('routes.publisher.get_destination', mock_get_destination)
    monkeypatch.setattr('routes.publisher._load_publish_destinations', mock_load_publish_destinations)
    monkeypatch.setattr('routes.publisher.bucket_path', mock_bucket_path)
    monkeypatch.setattr('routes.bucketer.bucket_path', mock_bucket_path)
    monkeypatch.setattr('routes.utils.sidecar_path', mock_sidecar_path)
    monkeypatch.setattr('routes.publisher.sidecar_path', mock_sidecar_path)
    monkeypatch.setattr('routes.utils.ensure_sidecar_for', mock_ensure_sidecar_for)
    monkeypatch.setattr('routes.publisher.ensure_sidecar_for', mock_ensure_sidecar_for)
    
    # Return the patched output directory for test use
    return setup_test_output_directory

def check_publishing_consistency(output_dir, bucket_id, filename, file_ext):
    """
    Check that all files and metadata are properly synchronized after publishing:
    1. bucket.json contains correct published_meta with metadata
    2. <bucket_id>.<ext>.json exists and contains direct metadata (no published wrapper)
    3. <bucket_id>.<ext> exists (the actual published file)
    
    This function is called from test functions that include "(same-bucket publishing)" 
    or "(cross-bucket publishing)" in their docstrings to indicate the test type.
    """
    # 1. Check bucket.json
    with open(output_dir / bucket_id / "bucket.json", "r") as f:
        bucket_meta = json.load(f)
        
    assert "published_meta" in bucket_meta, "Missing published_meta in bucket.json"
    assert "filename" in bucket_meta["published_meta"], "Missing filename in published_meta"
    assert bucket_meta["published_meta"]["filename"] == filename, "Wrong filename in published_meta"
    
    # Check that metadata is properly stored in published_meta
    assert "metadata" in bucket_meta["published_meta"], "Missing metadata in published_meta"
    assert "type" in bucket_meta["published_meta"]["metadata"], "Missing type in published_meta.metadata"
    
    # Check that URLs are properly set
    assert "raw_url" in bucket_meta["published_meta"], "Missing raw_url in published_meta"
    assert "thumbnail_url" in bucket_meta["published_meta"], "Missing thumbnail_url in published_meta"
    
    # Get URL values from the metadata
    raw_url = bucket_meta["published_meta"]["raw_url"]
    thumbnail_url = bucket_meta["published_meta"]["thumbnail_url"]
    
    # Determine test type by inspecting the caller's docstring
    import inspect
    caller_frame = inspect.currentframe().f_back
    caller_function = inspect.getframeinfo(caller_frame).function
    caller_docstring = globals()[caller_function].__doc__
    
    # Check if this is same-bucket or cross-bucket based on the docstring
    is_same_bucket = "(same-bucket publishing)" in caller_docstring if caller_docstring else False
    is_cross_bucket = "(cross-bucket publishing)" in caller_docstring if caller_docstring else False
    is_direct_publishing = not (is_same_bucket or is_cross_bucket)  # URL-like publishing
    
    if is_same_bucket:
        # Case A>A: Same bucket publishing
        expected_raw_url = f"/output/{bucket_id}/{filename}"
        expected_thumbnail_url = f"/output/{bucket_id}/thumbnails/{filename}"
        assert raw_url == expected_raw_url, f"Same-bucket raw_url incorrect. Expected {expected_raw_url}, got {raw_url}"
        assert thumbnail_url == expected_thumbnail_url, f"Same-bucket thumbnail_url incorrect. Expected {expected_thumbnail_url}, got {thumbnail_url}"
    else:
        # Case B>A, X>A: Cross-bucket or direct publishing
        expected_raw_url = f"/output/{bucket_id}{file_ext}"
        assert raw_url == expected_raw_url, f"Cross-bucket raw_url incorrect. Expected {expected_raw_url}, got {raw_url}"
        
        # Check thumbnail URL based on file type
        if file_ext.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            # For images, thumbnail should be the same as raw_url
            assert thumbnail_url == raw_url, f"Image thumbnail_url should match raw_url, got: {thumbnail_url}"
        elif file_ext.lower() in ['.mp4', '.webm', '.mov']:
            # For videos, thumbnail should use the jpg_from_mp4 endpoint
            expected_thumbnail_url = f"/api/generate/jpg_from_mp4?file={raw_url}"
            assert thumbnail_url == expected_thumbnail_url, f"Video thumbnail_url incorrect. Expected {expected_thumbnail_url}, got {thumbnail_url}"
    
    # 2. Check <bucket_id>.<ext>.json (the published info file)
    info_path = output_dir / f"{bucket_id}{file_ext}.json"
    assert info_path.exists(), f"Missing published info file: {info_path}"
    
    with open(info_path, "r") as f:
        published_info = json.load(f)
    
    # Published info should directly contain metadata (type, prompt, etc.)
    assert "type" in published_info, "Missing type in published info"
    
    # 3. Check published file
    published_file = output_dir / f"{bucket_id}{file_ext}"
    assert published_file.exists(), f"Published file not found: {published_file}"
    
    # 4. Ensure metadata is consistent between bucket.json and standalone file
    bucket_metadata_type = bucket_meta["published_meta"]["metadata"]["type"]
    standalone_metadata_type = published_info["type"]
    assert bucket_metadata_type == standalone_metadata_type, f"Metadata type mismatch between bucket.json ({bucket_metadata_type}) and standalone file ({standalone_metadata_type})"
    
    return True

# Test Cases

def test_publish_jpg_to_same_bucket(patch_publisher_functions):
    """Test publishing a JPG image from bucket_a to bucket_a (same-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    
    # Remove any existing sidecar for the source file
    sidecar_file = output_dir / f"{TEST_JPG_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    # Create custom metadata
    custom_metadata = {
        "type": "image", 
        "prompt": "Test JPG in bucket A"
    }
    
    # First add the JPG to bucket_a
    source_file = output_dir / TEST_JPG_FILENAME
    bucket_file = _append_to_bucket("bucket_a", source_file, custom_metadata)
    
    # Now publish from bucket_a to bucket_a
    result = publish_to_destination(
        source=bucket_file,
        publish_destination_id="bucket_a",
        metadata=custom_metadata,  # Pass explicit metadata
        skip_bucket=True,  # Skip adding to bucket since it's already there
    )
    
    assert result["success"], f"Publishing failed: {result.get('error', 'Unknown error')}"
    
    # Check consistency
    assert check_publishing_consistency(output_dir, "bucket_a", bucket_file.name, ".jpg")
    
    # Get published info and validate content
    published_info = get_published_info("bucket_a")
    assert published_info["published"] == bucket_file.name
    assert "prompt" in published_info["meta"]
    assert published_info["meta"]["prompt"] == "Test JPG in bucket A"

def test_publish_mp4_to_same_bucket(patch_publisher_functions):
    """Test publishing an MP4 video from bucket_a to bucket_a (same-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    
    # Remove any existing sidecar for the source file
    sidecar_file = output_dir / f"{TEST_MP4_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    # Create custom metadata
    custom_metadata = {
        "type": "video",
        "prompt": "Test MP4 in bucket A"
    }
    
    # First add the MP4 to bucket_a
    source_file = output_dir / TEST_MP4_FILENAME
    bucket_file = _append_to_bucket("bucket_a", source_file, custom_metadata)
    
    # Now publish from bucket_a to bucket_a
    result = publish_to_destination(
        source=bucket_file,
        publish_destination_id="bucket_a",
        metadata=custom_metadata,  # Pass explicit metadata
        skip_bucket=True,  # Skip adding to bucket since it's already there
    )
    
    assert result["success"], f"Publishing failed: {result.get('error', 'Unknown error')}"
    
    # Check consistency
    assert check_publishing_consistency(output_dir, "bucket_a", bucket_file.name, ".mp4")
    
    # Get published info and validate content
    published_info = get_published_info("bucket_a")
    assert published_info["published"] == bucket_file.name
    assert "prompt" in published_info["meta"]
    assert published_info["meta"]["prompt"] == "Test MP4 in bucket A"

def test_publish_jpg_from_bucket_b_to_bucket_a(patch_publisher_functions):
    """Test publishing a JPG image from bucket_b to bucket_a (cross-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    bucket_b_dir = patch_publisher_functions["bucket_b_dir"]
    
    # Remove any existing sidecar for the source file
    sidecar_file = output_dir / f"{TEST_JPG_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    # Create custom metadata
    custom_metadata = {
        "type": "image",
        "prompt": "Test JPG in bucket B"
    }
    
    # First add the JPG to bucket_b
    source_file = output_dir / TEST_JPG_FILENAME
    bucket_b_file = _append_to_bucket("bucket_b", source_file, custom_metadata)
    
    # Now publish from bucket_b to bucket_a
    result = publish_to_destination(
        source=bucket_b_file,
        publish_destination_id="bucket_a",
        metadata=custom_metadata,  # Pass explicit metadata
        skip_bucket=False,  # We want to add to bucket_a
        cross_bucket_mode=True  # Indicate this is cross-bucket publishing
    )
    
    assert result["success"], f"Publishing failed: {result.get('error', 'Unknown error')}"
    
    # The filename in bucket_a should be different from the one in bucket_b
    bucket_a_meta = load_meta("bucket_a")
    assert len(bucket_a_meta["sequence"]) > 0, "No files added to bucket_a"
    bucket_a_file = bucket_a_meta["sequence"][-1]
    assert bucket_a_file != bucket_b_file.name, "File not renamed when copying to bucket_a"
    
    # Check consistency
    assert check_publishing_consistency(output_dir, "bucket_a", bucket_a_file, ".jpg")
    
    # Get published info and validate content
    published_info = get_published_info("bucket_a")
    assert published_info["published"] == bucket_a_file
    assert "prompt" in published_info["meta"]
    assert published_info["meta"]["prompt"] == "Test JPG in bucket B"

def test_publish_mp4_from_bucket_b_to_bucket_a(patch_publisher_functions):
    """Test publishing an MP4 video from bucket_b to bucket_a (cross-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    bucket_b_dir = patch_publisher_functions["bucket_b_dir"]
    
    # Remove any existing sidecar for the source file
    sidecar_file = output_dir / f"{TEST_MP4_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    # Create custom metadata
    custom_metadata = {
        "type": "video",
        "prompt": "Test MP4 in bucket B"
    }
    
    # First add the MP4 to bucket_b
    source_file = output_dir / TEST_MP4_FILENAME
    bucket_b_file = _append_to_bucket("bucket_b", source_file, custom_metadata)
    
    # Now publish from bucket_b to bucket_a
    result = publish_to_destination(
        source=bucket_b_file,
        publish_destination_id="bucket_a",
        metadata=custom_metadata,  # Pass explicit metadata
        skip_bucket=False,  # We want to add to bucket_a
        cross_bucket_mode=True  # Indicate this is cross-bucket publishing
    )
    
    assert result["success"], f"Publishing failed: {result.get('error', 'Unknown error')}"
    
    # The filename in bucket_a should be different from the one in bucket_b
    bucket_a_meta = load_meta("bucket_a")
    assert len(bucket_a_meta["sequence"]) > 0, "No files added to bucket_a"
    bucket_a_file = bucket_a_meta["sequence"][-1]
    assert bucket_a_file != bucket_b_file.name, "File not renamed when copying to bucket_a"
    
    # Check consistency
    assert check_publishing_consistency(output_dir, "bucket_a", bucket_a_file, ".mp4")
    
    # Get published info and validate content
    published_info = get_published_info("bucket_a")
    assert published_info["published"] == bucket_a_file
    assert "prompt" in published_info["meta"]
    assert published_info["meta"]["prompt"] == "Test MP4 in bucket B"

def test_publish_sequence_jpg_then_mp4(patch_publisher_functions):
    """Test publishing a JPG and then an MP4 to the same bucket (bucket_a, same-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    
    # Remove any existing sidecars
    jpg_sidecar = output_dir / f"{TEST_JPG_FILENAME}.json"
    mp4_sidecar = output_dir / f"{TEST_MP4_FILENAME}.json"
    if jpg_sidecar.exists():
        jpg_sidecar.unlink()
    if mp4_sidecar.exists():
        mp4_sidecar.unlink()
    
    # Create custom metadata for JPG
    jpg_metadata = {
        "type": "image",
        "prompt": "Test JPG in sequence"
    }
    
    # First publish a JPG
    jpg_source = output_dir / TEST_JPG_FILENAME
    jpg_result = publish_to_destination(
        source=jpg_source,
        publish_destination_id="bucket_a",
        metadata=jpg_metadata,  # Pass explicit metadata
        skip_bucket=False,
    )
    
    assert jpg_result["success"], "Failed to publish JPG"
    jpg_filename = jpg_result["meta"]["filename"]
    
    # Get published info after JPG publish
    jpg_info = get_published_info("bucket_a")
    assert jpg_info["published"] == jpg_filename
    assert jpg_info["meta"]["type"] == "image"
    
    # Now publish an MP4 to the same bucket
    mp4_metadata = {
        "type": "video",
        "prompt": "Test MP4 after JPG"
    }
    
    mp4_source = output_dir / TEST_MP4_FILENAME
    mp4_result = publish_to_destination(
        source=mp4_source,
        publish_destination_id="bucket_a",
        metadata=mp4_metadata,  # Pass explicit metadata
        skip_bucket=False,
    )
    
    assert mp4_result["success"], "Failed to publish MP4"
    mp4_filename = mp4_result["meta"]["filename"]
    
    # Get published info after MP4 publish
    mp4_info = get_published_info("bucket_a")
    assert mp4_info["published"] == mp4_filename
    assert mp4_info["meta"]["type"] == "video"
    
    # Check that the MP4 replaced the JPG in published_meta
    assert jpg_filename != mp4_filename
    
    # Check the standalone published file info
    standalone_info_path = output_dir / "bucket_a.mp4.json"
    assert standalone_info_path.exists()
    
    with open(standalone_info_path, "r") as f:
        standalone_info = json.load(f)
    
    assert standalone_info["type"] == "video"

def test_publish_sequence_mp4_then_jpg(patch_publisher_functions):
    """Test publishing an MP4 and then a JPG to the same bucket (bucket_a, same-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    
    # Remove any existing sidecars
    jpg_sidecar = output_dir / f"{TEST_JPG_FILENAME}.json"
    mp4_sidecar = output_dir / f"{TEST_MP4_FILENAME}.json"
    if jpg_sidecar.exists():
        jpg_sidecar.unlink()
    if mp4_sidecar.exists():
        mp4_sidecar.unlink()
    
    # Create custom metadata for MP4
    mp4_metadata = {
        "type": "video",
        "prompt": "Test MP4 in sequence"
    }
    
    # First publish an MP4
    mp4_source = output_dir / TEST_MP4_FILENAME
    mp4_result = publish_to_destination(
        source=mp4_source,
        publish_destination_id="bucket_a",
        metadata=mp4_metadata,  # Pass explicit metadata
        skip_bucket=False,
    )
    
    assert mp4_result["success"], "Failed to publish MP4"
    mp4_filename = mp4_result["meta"]["filename"]
    
    # Get published info after MP4 publish
    mp4_info = get_published_info("bucket_a")
    assert mp4_info["published"] == mp4_filename
    assert mp4_info["meta"]["type"] == "video"
    
    # Now publish a JPG to the same bucket
    jpg_metadata = {
        "type": "image",
        "prompt": "Test JPG after MP4"
    }
    
    jpg_source = output_dir / TEST_JPG_FILENAME
    jpg_result = publish_to_destination(
        source=jpg_source,
        publish_destination_id="bucket_a",
        metadata=jpg_metadata,  # Pass explicit metadata
        skip_bucket=False,
    )
    
    assert jpg_result["success"], "Failed to publish JPG"
    jpg_filename = jpg_result["meta"]["filename"]
    
    # Get published info after JPG publish
    jpg_info = get_published_info("bucket_a")
    assert jpg_info["published"] == jpg_filename
    assert jpg_info["meta"]["type"] == "image"
    
    # Check that the JPG replaced the MP4 in published_meta
    assert jpg_filename != mp4_filename
    
    # Check the standalone published file info
    standalone_info_path = output_dir / "bucket_a.jpg.json"
    assert standalone_info_path.exists()
    
    with open(standalone_info_path, "r") as f:
        standalone_info = json.load(f)
    
    assert standalone_info["type"] == "image"

def test_publish_without_bucket_copy(patch_publisher_functions):
    """Test publishing directly to a bucket without first adding to bucket (direct publishing, like URL sources)."""
    output_dir = patch_publisher_functions["output_dir"]
    
    # Use a source file not already in a bucket
    source_file = output_dir / TEST_JPG_FILENAME
    
    # Remove any existing sidecar to prevent metadata interference
    sidecar_file = output_dir / f"{TEST_JPG_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    # Create a custom metadata that's different from the sidecar
    custom_metadata = {
        "type": "image",
        "prompt": "Direct publish without bucket"
    }
    
    # Publish directly to bucket_a without appending to bucket
    result = publish_to_destination(
        source=source_file,
        publish_destination_id="bucket_a",
        metadata=custom_metadata,  # Use our custom metadata
        skip_bucket=True,  # Skip adding to bucket
        cross_bucket_mode=True  # Direct publishing uses cross-bucket mode
    )
    
    assert result["success"], f"Publishing failed: {result.get('error', 'Unknown error')}"
    
    # Verify the file was published but not added to the bucket
    bucket_a_meta = load_meta("bucket_a")
    assert len(bucket_a_meta["sequence"]) == 0, "File was incorrectly added to bucket"
    
    # Check that published info file was properly created
    published_info_path = output_dir / "bucket_a.jpg.json"
    assert published_info_path.exists(), "Published info file not created"
    
    # Check the direct file content
    with open(published_info_path, "r") as f:
        published_info = json.load(f)
    
    # Verify the metadata was written correctly to the file
    assert "type" in published_info, "Missing type in published metadata"
    assert "prompt" in published_info, "Missing prompt in published metadata"
    assert published_info["prompt"] == "Direct publish without bucket", \
        f"Wrong prompt in metadata file. Expected 'Direct publish without bucket', got '{published_info.get('prompt', 'missing')}'"

# Additional edge case tests

def test_publish_nonexistent_file(patch_publisher_functions):
    """Test publishing a file that doesn't exist."""
    output_dir = patch_publisher_functions["output_dir"]
    
    # Create a path to a nonexistent file
    nonexistent_file = output_dir / "nonexistent.jpg"
    
    # Try to publish - should fail
    result = publish_to_destination(
        source=nonexistent_file,
        publish_destination_id="bucket_a",
    )
    
    assert not result["success"], "Publishing should fail for nonexistent file"
    assert "error" in result, "Error message should be present"

def test_publish_changing_metadata(patch_publisher_functions):
    """Test that metadata is updated when publishing the same file multiple times (same-bucket publishing)."""
    output_dir = patch_publisher_functions["output_dir"]
    bucket_a_dir = patch_publisher_functions["bucket_a_dir"]
    
    # Add a JPG to bucket_a with specific metadata 
    jpg_source = output_dir / TEST_JPG_FILENAME
    
    # Remove any existing sidecar to prevent metadata interference
    sidecar_file = output_dir / f"{TEST_JPG_FILENAME}.json"
    if sidecar_file.exists():
        sidecar_file.unlink()
    
    first_metadata = {
        "type": "image",
        "prompt": "First metadata"
    }
    
    # Ensure we create the file with our custom metadata
    jpg_file = _append_to_bucket("bucket_a", jpg_source, first_metadata)
    
    # Also create the sidecar file with the same metadata
    with open(bucket_a_dir / f"{jpg_file.name}.json", "w") as f:
        json.dump(first_metadata, f, indent=2)
    
    # Publish with the first metadata
    result1 = publish_to_destination(
        source=jpg_file,
        publish_destination_id="bucket_a",
        metadata=first_metadata,  # Include explicit metadata
        skip_bucket=True,
    )
    
    assert result1["success"], "First publish failed"
    
    # Get published info to verify first metadata
    published_info1 = get_published_info("bucket_a")
    assert published_info1["meta"]["prompt"] == "First metadata"
    
    # Update the sidecar for the file with new metadata
    second_metadata = {
        "type": "image",
        "prompt": "Updated metadata"
    }
    
    with open(bucket_a_dir / f"{jpg_file.name}.json", "w") as f:
        json.dump(second_metadata, f, indent=2)
    
    # Publish the same file again with explicit metadata
    result2 = publish_to_destination(
        source=jpg_file,
        publish_destination_id="bucket_a",
        metadata=second_metadata,  # Include explicit updated metadata
        skip_bucket=True,
    )
    
    assert result2["success"], "Second publish failed"
    
    # Verify metadata was updated
    published_info2 = get_published_info("bucket_a")
    assert published_info2["meta"]["prompt"] == "Updated metadata", "Metadata was not updated" 