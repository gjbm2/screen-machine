# Reference Image Persistence Design

## Problem Statement

Currently, the "Create another" functionality in the Recent tab works only for text-only prompts. When users generate images using reference images (uploaded files or camera inputs), clicking "Create another" fails because:

1. **Blob URL Expiration**: Reference images from File uploads create blob URLs that expire after the generation completes
2. **No Persistent Storage**: Reference images are not stored alongside generated images in buckets
3. **Metadata Limitation**: The current metadata only stores `referenceUrls` as comma-separated strings pointing to expired blob URLs

## Current Architecture Analysis

### Bucket Structure
```
output/
├── _recent/
│   ├── 20241215-abc123.jpg              # Generated image
│   ├── 20241215-abc123.jpg.json         # Metadata sidecar
│   ├── thumbnails/
│   │   └── 20241215-abc123.jpg.jpg      # Thumbnail
│   └── bucket.json                      # Bucket metadata with sequence
```

### Current Generation Flow
1. **Frontend**: `generateImage()` → `processUploadedFiles()` → creates blob URLs for File objects
2. **API**: `encode_reference_urls()` downloads/compresses reference images to base64
3. **Backend**: `handle_image_generation()` processes generation with reference images
4. **Storage**: `save_to_recent()` → `_append_to_bucket()` saves only the generated image + metadata

### Current Metadata Flow
1. **Generation**: `referenceUrls` stored as comma-separated string in metadata
2. **Storage**: Only the generated image and its metadata are saved to `_recent`
3. **Retrieval**: `handleGenerateAgain` reads metadata and attempts to use expired blob URLs

### Critical Architecture Insights

#### CRUD Operations Pattern
The existing bucket system follows a consistent pattern:
- **Create**: `_append_to_bucket()` handles main file, sidecar, thumbnail, and sequence metadata
- **Read**: `get_bucket_complete()` serves files with metadata and thumbnails
- **Update**: Sequence reordering, favorites, and metadata updates
- **Delete**: `delete_file()` removes main file, sidecar, and thumbnail atomically

#### Metadata Management
- **Sidecar Pattern**: Each file has a `.json` sidecar with metadata
- **Sequence Tracking**: `bucket.json` contains sequence array (strings or dicts with `batchId`)
- **Thumbnail Generation**: Automatic thumbnail creation in `thumbnails/` subdirectory
- **Batch Support**: `batchId` tracking for grouping related images

#### Reference Image Processing
- **Encoding**: `encode_reference_urls()` handles URL downloads and base64 conversion
- **File Uploads**: `encode_image_uploads()` processes uploaded files
- **Compression**: Automatic JPEG conversion and size optimization
- **Naming**: Proper filename generation for API consumption

### Current Limitations
- Reference images are not persisted beyond generation
- Blob URLs become invalid after browser refresh/navigation
- No stable addressing for reference images in subsequent generations
- `handleGenerateAgain` cannot access original reference images

## Proposed Solution: Reference Image Persistence

### Architecture Overview

Implement a system where reference images are persisted alongside generated images in buckets, providing stable, persistent references for "Create another" functionality.

### File Structure Design

```
output/
├── _recent/
│   ├── 20241215-abc123.jpg              # Generated image
│   ├── 20241215-abc123.jpg.json         # Metadata sidecar
│   ├── 20241215-abc123.jpg.ref.1        # First reference image
│   ├── 20241215-abc123.jpg.ref.2        # Second reference image
│   ├── 20241215-abc123.jpg.ref.N        # Nth reference image
│   ├── thumbnails/
│   │   ├── 20241215-abc123.jpg.jpg      # Generated image thumbnail
│   │   ├── 20241215-abc123.jpg.ref.1.jpg # Reference image 1 thumbnail
│   │   └── 20241215-abc123.jpg.ref.2.jpg # Reference image 2 thumbnail
│   └── bucket.json                      # Bucket metadata
```

### Metadata Structure Enhancement

```json
{
  "id": "abc123",
  "url": "/output/_recent/20241215-abc123.jpg",
  "prompt": "refined prompt text",
  "original_prompt": "original user prompt",
  "workflow": "flux1",
  "seed": 12345,
  "timestamp": 1734278400000,
  "batch_id": "batch_xyz",
  "reference_images": [
    {
      "index": 1,
      "original_filename": "IMG_1234.jpg",
      "stored_path": "/output/_recent/20241215-abc123.jpg.ref.1",
      "thumbnail_path": "/output/_recent/thumbnails/20241215-abc123.jpg.ref.1.jpg",
      "content_type": "image/jpeg",
      "size": 2048576,
      "source_type": "file_upload" // or "camera", "url", "generated"
    },
    {
      "index": 2,
      "original_filename": "reference.png",
      "stored_path": "/output/_recent/20241215-abc123.jpg.ref.2",
      "thumbnail_path": "/output/_recent/thumbnails/20241215-abc123.jpg.ref.2.jpg",
      "content_type": "image/png",
      "size": 1024000,
      "source_type": "url"
    }
  ],
  "refiner": "enrich",
  "refiner_params": {...},
  "params": {...},
  "global_params": {...}
}
```

## Implementation Plan

### Phase 1: Backend Infrastructure

#### 1.1 Reference Image Storage Service
**Location**: `routes/bucket_utils.py`

```python
class ReferenceImageStorage:
    def store_reference_images(self, base_filename: str, bucket_id: str, 
                             reference_sources: List[ReferenceSource]) -> List[ReferenceImageInfo]:
        """Store reference images alongside generated image"""
        
    def get_reference_images(self, base_filename: str, bucket_id: str) -> List[ReferenceImageInfo]:
        """Retrieve reference images for a generated image"""
        
    def cleanup_reference_images(self, base_filename: str, bucket_id: str):
        """Clean up reference images when main image is deleted"""
```

#### 1.2 Bucket API Extensions
**Location**: `routes/bucket_api.py`

- Extend `delete_file()` to handle reference image cleanup
- Extend `get_bucket_complete()` to include reference image information
- Add reference image serving endpoints

#### 1.3 Generation Pipeline Integration
**Location**: `routes/generate_utils.py`

Modify `save_to_recent()` to:
1. Store reference images alongside generated images
2. Update metadata with reference image paths
3. Generate thumbnails for reference images

### Phase 2: Frontend Infrastructure

#### 2.1 API Service Updates
**Location**: `src/utils/api.ts`

```typescript
interface ReferenceImageInfo {
  index: number;
  original_filename: string;
  stored_path: string;
  thumbnail_path: string;
  content_type: string;
  size: number;
  source_type: 'file_upload' | 'camera' | 'url' | 'generated';
}

interface BucketItemMetadata {
  // ... existing fields
  reference_images?: ReferenceImageInfo[];
}
```

#### 2.2 Reference Image Retrieval Service
**Location**: `src/services/reference-image-service.ts`

```typescript
export class ReferenceImageService {
  async getReferenceImagesForGeneration(bucketId: string, filename: string): Promise<string[]>;
  async validateReferenceImageAccess(paths: string[]): Promise<boolean>;
}
```

#### 2.3 Generate Again Enhancement
**Location**: `src/components/recent/RecentView.tsx`

Update `handleGenerateAgain()` to:
1. Retrieve reference images from metadata
2. Convert stored paths to accessible URLs
3. Pass reference images to generation pipeline

### Phase 3: CRUD Operations

#### 3.1 Create Operations
- Store reference images during generation
- Create thumbnails for reference images
- Update metadata with reference image information

#### 3.2 Read Operations
- Serve reference images via bucket API
- Include reference image info in bucket listings
- Provide reference image thumbnails

#### 3.3 Update Operations
- Handle reference image updates if main image is modified
- Maintain consistency between main image and references

#### 3.4 Delete Operations
- Clean up reference images when main image is deleted
- Remove reference image thumbnails
- Update bucket metadata

## Critical Implementation Details

### Backend Integration Points

#### 1. Generation Pipeline Modification
**File**: `routes/generate_utils.py`
- Modify `save_to_recent()` to accept reference image data
- Store reference images before calling `_append_to_bucket()`
- Update metadata to include reference image paths

#### 2. Bucket Operations Extension
**File**: `routes/bucket_api.py`
- Extend `delete_file()` to clean up reference images:
  ```python
  # Clean up reference images
  ref_pattern = bucket_path(bucket_id) / f"{Path(filename).stem}.ref.*"
  for ref_file in glob.glob(str(ref_pattern)):
      Path(ref_file).unlink(missing_ok=True)
  ```

#### 3. Reference Image Serving
**File**: `routes/bucket_api.py`
- Add endpoint to serve reference images with proper content types
- Implement access control consistent with main images

### Frontend Integration Points

#### 1. Generate Again Logic
**File**: `src/components/recent/RecentView.tsx`
- Modify `handleGenerateAgain()` to extract reference images from metadata
- Convert stored paths to accessible URLs for the generation API

#### 2. API Service Updates
**File**: `src/utils/api.ts`
- Update `BucketItemMetadata` interface to include reference images
- Modify `getBucketDetails()` to return reference image information

#### 3. Reference Image Processing
**File**: `src/hooks/image-generation/api/reference-image-utils.ts`
- Add function to convert stored reference images back to URLs
- Handle reference image validation and access checks

## Technical Considerations

### File Naming Convention
- **Pattern**: `{base_filename}.ref.{index}`
- **Example**: `20241215-abc123.jpg.ref.1`
- **Benefits**: 
  - Clear association with main image
  - Supports multiple reference images
  - Maintains file extension for proper serving

### Storage Efficiency
- **Deduplication**: Check if identical reference images already exist
- **Compression**: Apply appropriate compression for reference images
- **Format Standardization**: Convert all reference images to JPEG for consistency

### Performance Optimization
- **Lazy Loading**: Load reference images only when needed
- **Thumbnail Generation**: Create thumbnails for quick preview
- **Caching**: Implement appropriate caching strategies

### Error Handling
- **Partial Failures**: Handle cases where some reference images fail to store
- **Cleanup**: Ensure orphaned reference images are cleaned up
- **Validation**: Validate reference image integrity before storage

## Migration Strategy

### Backward Compatibility
- Existing images without reference images continue to work
- Graceful degradation for legacy metadata format
- Optional migration script for existing data

## Testing Strategy

### Unit Tests
- **ReferenceImageStorage.store_reference_images()**: Test storing reference images with valid inputs
- **ReferenceImageStorage.get_reference_images()**: Test retrieving reference images for existing files
- **ReferenceImageStorage.cleanup_reference_images()**: Test cleanup when main image is deleted
- **Metadata handling**: Test adding/removing reference image metadata from JSON sidecars
- **File operations**: Test creating reference image files with correct naming patterns
- **Error handling**: Test behavior with invalid file paths, missing files, and corrupted metadata

## Alternative Approaches Considered

### Approach A: Blob URL Extension
**Pros**: Minimal changes to existing architecture
**Cons**: Still temporary, browser-dependent, not persistent across sessions

### Approach C: Separate Reference Image Bucket
**Pros**: Clean separation of concerns
**Cons**: Complex cross-bucket relationships, harder to maintain consistency

## Design Validation

### Architecture Consistency ✅
- Follows existing bucket patterns (main file + sidecar + thumbnail)
- Leverages existing CRUD operations in `bucket_api.py`
- Uses established metadata management patterns

### Implementation Feasibility ✅
- Builds on existing `_append_to_bucket()` functionality
- Extends proven thumbnail generation system
- Reuses established file serving patterns

### Performance Impact ✅
- Minimal overhead: reference images stored once per generation
- Efficient retrieval: direct file serving from bucket
- Thumbnail system already handles multiple files per image

### Backward Compatibility ✅
- Existing images continue to work without modification
- Graceful degradation for legacy metadata format
- No breaking changes to existing APIs

## Conclusion

The proposed reference image persistence approach provides:
- **Stable References**: Persistent URLs that survive browser sessions
- **Consistent Architecture**: Follows existing bucket patterns
- **Comprehensive CRUD**: Full lifecycle management for reference images
- **Performance**: Efficient storage and retrieval
- **Backward Compatibility**: Works with existing images

This design enables robust "Create another" functionality while maintaining the existing architecture patterns and providing a foundation for future reference image features.

## Implementation Priority

**High Priority**: Backend infrastructure (Phase 1)
- Critical for core functionality
- Establishes foundation for frontend integration

**Medium Priority**: Frontend integration (Phase 2)
- Enables user-facing features
- Builds on backend foundation

**Low Priority**: Advanced features and optimization (Phase 3)
- Performance enhancements
- Additional UI features

