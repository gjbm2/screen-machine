"""
Bucket utilities for reference image persistence
"""
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import shutil
import base64
from io import BytesIO
from PIL import Image
import tempfile

from utils.logger import info, error, warning, debug
from routes.bucketer import bucket_path, load_meta, save_meta
from routes.utils import generate_thumbnail, sidecar_path


class ReferenceSource:
    """Represents a reference image source"""
    def __init__(self, content: bytes, original_filename: str, content_type: str = "image/jpeg", source_type: str = "file_upload"):
        self.content = content
        self.original_filename = original_filename
        self.content_type = content_type
        self.source_type = source_type


class ReferenceImageInfo:
    """Represents stored reference image information"""
    def __init__(self, index: int, original_filename: str, stored_path: str, 
                 thumbnail_path: str, content_type: str, size: int, source_type: str):
        self.index = index
        self.original_filename = original_filename
        self.stored_path = stored_path
        self.thumbnail_path = thumbnail_path
        self.content_type = content_type
        self.size = size
        self.source_type = source_type

    def to_dict(self, bucket_id: str = None) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        if bucket_id:
            # Convert relative paths to full static URLs for API responses
            stored_url = f"/output/{bucket_id}/{self.stored_path}"
            thumbnail_url = f"/output/{bucket_id}/{self.thumbnail_path}"
        else:
            # Keep relative paths for storage
            stored_url = self.stored_path
            thumbnail_url = self.thumbnail_path
            
        return {
            "index": self.index,
            "original_filename": self.original_filename,
            "stored_path": stored_url,
            "thumbnail_path": thumbnail_url,
            "content_type": self.content_type,
            "size": self.size,
            "source_type": self.source_type
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReferenceImageInfo':
        """Create from dictionary"""
        # Add defensive checks for missing fields
        required_fields = ["index", "original_filename", "stored_path", "thumbnail_path", "content_type", "size", "source_type"]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            raise ValueError(f"Missing required fields in reference image data: {missing_fields}. Available fields: {list(data.keys())}")
        
        return cls(
            index=data["index"],
            original_filename=data["original_filename"],
            stored_path=data["stored_path"],
            thumbnail_path=data["thumbnail_path"],
            content_type=data["content_type"],
            size=data["size"],
            source_type=data["source_type"]
        )


class ReferenceImageStorage:
    """Service for storing and managing reference images alongside generated images"""
    
    def store_reference_images(self, base_filename: str, bucket_id: str, 
                             reference_sources: List[ReferenceSource]) -> List[ReferenceImageInfo]:
        """
        Store reference images alongside generated image
        
        Args:
            base_filename: The filename of the generated image (without extension)
            bucket_id: The bucket ID where images are stored
            reference_sources: List of reference image sources to store
            
        Returns:
            List of ReferenceImageInfo objects for stored reference images
        """
        if not reference_sources:
            return []
            
        bucket_dir = bucket_path(bucket_id)
        if not bucket_dir.exists():
            error(f"Bucket directory does not exist: {bucket_dir}")
            return []
            
        stored_references = []
        
        for i, source in enumerate(reference_sources, 1):
            try:
                # Generate reference image filename
                ref_filename = f"{base_filename}.ref.{i}"
                ref_path = bucket_dir / ref_filename
                
                # Store the reference image
                ref_path.write_bytes(source.content)
                
                # Generate thumbnail
                thumb_dir = bucket_dir / "thumbnails"
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_path = thumb_dir / f"{ref_filename}.jpg"
                
                try:
                    # Generate thumbnail from the stored file
                    generate_thumbnail(ref_path, thumb_path)
                except Exception as e:
                    warning(f"Failed to generate thumbnail for reference image {ref_filename}: {e}")
                    # Create empty thumbnail file to maintain consistency
                    thumb_path.touch()
                
                # Create reference image info
                ref_info = ReferenceImageInfo(
                    index=i,
                    original_filename=source.original_filename,
                    stored_path=ref_filename,  # Use relative path (just filename)
                    thumbnail_path=f"thumbnails/{ref_filename}.jpg",  # Use relative path
                    content_type=source.content_type,
                    size=len(source.content),
                    source_type=source.source_type
                )
                
                stored_references.append(ref_info)
                info(f"Stored reference image {i}: {ref_filename}")
                
            except Exception as e:
                error(f"Failed to store reference image {i}: {e}")
                continue
        
        # Note: Reference image metadata is stored in the sidecar file by the caller
        
        return stored_references
    
    def get_reference_images(self, base_filename: str, bucket_id: str) -> List[ReferenceImageInfo]:
        """
        Retrieve reference images for a generated image
        
        Args:
            base_filename: The filename of the generated image (without extension)
            bucket_id: The bucket ID where images are stored
            
        Returns:
            List of ReferenceImageInfo objects for reference images
        """
        bucket_dir = bucket_path(bucket_id)
        if not bucket_dir.exists():
            return []
            
        # First try to load from sidecar file (preferred method)
        from routes.utils import sidecar_path
        
        # Find the main image file to get its sidecar
        main_image_path = None
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            potential_path = bucket_dir / f"{base_filename}{ext}"
            if potential_path.exists():
                main_image_path = potential_path
                break
        
        if main_image_path:
            sidecar_file = sidecar_path(main_image_path)
            if sidecar_file.exists():
                try:
                    with open(sidecar_file, 'r', encoding='utf-8') as f:
                        sidecar_data = json.load(f)
                    
                    ref_data_list = sidecar_data.get("reference_images", [])
                    stored_references = []
                    
                    for ref_data in ref_data_list:
                        try:
                            ref_info = ReferenceImageInfo.from_dict(ref_data)
                            
                            # Handle different path formats for backward compatibility
                            stored_path = ref_info.stored_path
                            thumbnail_path = ref_info.thumbnail_path
                            
                            # Remove bucket prefix if present (e.g., "_recent/filename" -> "filename")
                            if stored_path.startswith(f'{bucket_id}/'):
                                stored_path = stored_path[len(f'{bucket_id}/'):]
                            elif stored_path.startswith('/'):
                                # Absolute path - convert to relative by taking just the filename
                                stored_path = Path(stored_path).name
                            
                            # Same for thumbnail path
                            if thumbnail_path.startswith(f'{bucket_id}/'):
                                thumbnail_path = thumbnail_path[len(f'{bucket_id}/'):]
                            elif thumbnail_path.startswith('/'):
                                # Absolute path - convert to relative
                                thumbnail_path = f"thumbnails/{Path(thumbnail_path).name}"
                            
                            # Update the ref_info with corrected paths
                            ref_info.stored_path = stored_path
                            ref_info.thumbnail_path = thumbnail_path
                            
                            # Check if the file exists with the corrected path
                            ref_path = bucket_dir / stored_path
                            if ref_path.exists():
                                stored_references.append(ref_info)
                            else:
                                warning(f"Reference image file not found: {ref_path}")
                                
                        except Exception as e:
                            error(f"Failed to load reference image info: {e}")
                            continue
                    
                    return stored_references
                except Exception as e:
                    error(f"Failed to read sidecar file {sidecar_file}: {e}")
        
        # Fallback to bucket metadata (legacy support)
        meta = load_meta(bucket_id)
        reference_images = meta.get("reference_images", {})
        base_refs = reference_images.get(base_filename, [])
        
        stored_references = []
        for ref_data in base_refs:
            try:
                ref_info = ReferenceImageInfo.from_dict(ref_data)
                
                # Handle different path formats for backward compatibility
                stored_path = ref_info.stored_path
                thumbnail_path = ref_info.thumbnail_path
                
                # Remove bucket prefix if present (e.g., "_recent/filename" -> "filename")
                if stored_path.startswith(f'{bucket_id}/'):
                    stored_path = stored_path[len(f'{bucket_id}/'):]
                elif stored_path.startswith('/'):
                    # Absolute path - convert to relative by taking just the filename
                    stored_path = Path(stored_path).name
                
                # Same for thumbnail path
                if thumbnail_path.startswith(f'{bucket_id}/'):
                    thumbnail_path = thumbnail_path[len(f'{bucket_id}/'):]
                elif thumbnail_path.startswith('/'):
                    # Absolute path - convert to relative
                    thumbnail_path = f"thumbnails/{Path(thumbnail_path).name}"
                
                # Update the ref_info with corrected paths
                ref_info.stored_path = stored_path
                ref_info.thumbnail_path = thumbnail_path
                
                # Check if the file exists with the corrected path
                ref_path = bucket_dir / stored_path
                if ref_path.exists():
                    stored_references.append(ref_info)
                else:
                    warning(f"Reference image file not found: {ref_path}")
                    
            except Exception as e:
                error(f"Failed to load reference image info: {e}")
                continue
        
        return stored_references
    
    def cleanup_reference_images(self, base_filename: str, bucket_id: str):
        """
        Clean up reference images when main image is deleted
        
        Args:
            base_filename: The filename of the generated image (without extension)
            bucket_id: The bucket ID where images are stored
        """
        bucket_dir = bucket_path(bucket_id)
        if not bucket_dir.exists():
            return
            
        # Get reference images to clean up
        ref_images = self.get_reference_images(base_filename, bucket_id)
        
        for ref_info in ref_images:
            try:
                # Delete reference image file - resolve relative path
                ref_path = bucket_dir / ref_info.stored_path
                if ref_path.exists():
                    ref_path.unlink()
                    info(f"Deleted reference image: {ref_path}")
                
                # Delete thumbnail - resolve relative path
                thumb_path = bucket_dir / ref_info.thumbnail_path
                if thumb_path.exists():
                    thumb_path.unlink()
                    info(f"Deleted reference thumbnail: {thumb_path}")
                    
            except Exception as e:
                error(f"Failed to cleanup reference image {ref_info.index}: {e}")
        
        # Remove reference image metadata
        meta = load_meta(bucket_id)
        reference_images = meta.get("reference_images", {})
        if base_filename in reference_images:
            del reference_images[base_filename]
            save_meta(bucket_id, meta)
            info(f"Cleaned up reference image metadata for: {base_filename}")


def update_metadata_with_references(metadata: Dict[str, Any], reference_images: List[ReferenceImageInfo]) -> Dict[str, Any]:
    """
    Update metadata with reference image information
    
    Args:
        metadata: Existing metadata dictionary
        reference_images: List of ReferenceImageInfo objects
        
    Returns:
        Updated metadata dictionary
    """
    if not reference_images:
        return metadata
    
    # Convert ReferenceImageInfo objects to dictionaries
    ref_data = [ref.to_dict() for ref in reference_images]
    
    # Add to metadata
    metadata["reference_images"] = ref_data
    
    return metadata


# Legacy function - metadata is now stored in sidecar files
def store_reference_images_in_metadata(bucket_id: str, base_filename: str, reference_images: List[ReferenceImageInfo]):
    """
    Store reference image information in bucket metadata (legacy - now stored in sidecar)
    
    Args:
        bucket_id: The bucket ID
        base_filename: The base filename (without extension)
        reference_images: List of ReferenceImageInfo objects
    """
    # This function is kept for backward compatibility but is no longer used
    # Reference images are now stored in sidecar files instead of bucket metadata
    pass 