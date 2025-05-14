# Recent Tab

## Overview

The Recent tab provides a centralized view of all recently generated images, organized by batch. Unlike the "Generated" tab, the Recent tab persists images between sessions and provides an improved interface for viewing, managing, and interacting with generated content.

## Key Features

- **Batch Organization**: Images are grouped by their generation batch
- **Persistence**: Images remain available for 24 hours even after reloading the browser
- **Drag & Drop**: Standard drag-and-drop affordances for copying to other buckets or using in prompts
- **Delete Options**: Easy image and batch deletion
- **Responsive Layout**: Adapts to different screen sizes with minimum 4 thumbnails per row
- **Generate Again**: Repeat previous generation with the same settings
- **Visual Modes**: Expanded or collapsed views for each batch

## User Interface

### Batch Panel States

Each batch panel has two states:

1. **Expanded** (default for newly generated batches):
   - Large selected image on top
   - Thumbnail strip below with all batch images
   - "Generate Again" button as the last thumbnail
   - Batch header shows "Batch 42 1/5" (batch ID, selected image, total count)
   - Drag handle for reordering
   - Menu with "Delete Batch" option

2. **Collapsed**:
   - Single selected image with "1/5" badge
   - Compact view to scan multiple batches at once

### Actions

- **Click thumbnail**: Select it as the main image
- **Click selected image**: Open in Loope view
- **Click "×"** on thumbnail: Delete that image
- **Click "Generate Again"**: Recreate batch with same settings
- **Drag image**: Drop onto prompt area or other bucket tabs
- **Drag handle**: Reorder batches
- **Context menu**: Copy to other buckets, delete, etc.

### Persistence

- Images remain in Recent tab for 24 hours
- Batch order and expanded/collapsed states are saved to localStorage
- New batches always appear expanded at the top of the list

## Technical Details

### Implementation

- Built on top of the existing bucket infrastructure
- Uses `_recent` pub_dest (hidden from UI outside Recent tab)
- Standard sequence.json for tracking images and batches
- Thumbnails generated and stored like in normal buckets

### Housekeeping

- `scripts/purge_recent.py` runs periodically to remove files older than 24h
- Handles cleanup of thumbnails, sidecars, and sequence.json entries

## Configuration

By default, the Recent tab is enabled. It can be disabled by setting the
environment variable:

```
RECENT_TAB_ENABLED=false
```

## Limitations

- No favorite functionality in Recent tab
- Cannot drag images between batches
- No sorting or filtering capabilities
- Images will be deleted after 24 hours (non-configurable without code change)

## Development

To modify or extend the Recent tab:

1. Components are in `src/components/recent/`
2. CSS is in `src/components/recent/recent.module.css`
3. Backend purge script is `scripts/purge_recent.py` 