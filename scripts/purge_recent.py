#!/usr/bin/env python3
"""
purge_recent.py

Removes files from the _recent bucket that are older than 24 hours.
Skips any files that are favorited (despite favorites being disabled in the UI).
This allows for future-proofing in case favorites are ever enabled.

Usage:
    python purge_recent.py [--hours N] [--dry-run]

Options:
    --hours N    Remove files older than N hours (default: 24)
    --dry-run    Print files that would be removed without deleting them
"""

import os
import sys
import json
import time
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Set base directories
BASE_DIR = Path("output").resolve()
RECENT_BUCKET = "_recent"
RECENT_DIR = BASE_DIR / RECENT_BUCKET
SEQUENCE_FILE = RECENT_DIR / "sequence.json"

def log(message):
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def load_sequence():
    """Load the sequence.json file, creating it if it doesn't exist."""
    if not SEQUENCE_FILE.exists():
        return {"order": [], "favorites": []}
    
    try:
        with open(SEQUENCE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        log(f"Error loading {SEQUENCE_FILE}, creating new sequence file")
        return {"order": [], "favorites": []}

def save_sequence(sequence):
    """Save the sequence data back to sequence.json."""
    os.makedirs(RECENT_DIR, exist_ok=True)
    with open(SEQUENCE_FILE, "w") as f:
        json.dump(sequence, f, indent=2)

def is_file_older_than(file_path, hours):
    """Check if a file is older than the specified number of hours."""
    if not file_path.exists():
        return False
    
    # Get file's creation time
    file_ctime = file_path.stat().st_ctime
    
    # Calculate age threshold
    threshold = time.time() - (hours * 3600)
    
    # Check if file is older than threshold
    return file_ctime < threshold

def is_file_too_new(file_path, minutes=3):
    """
    Check if a file is too new (less than X minutes old).
    This prevents deleting files that might still be in use.
    """
    if not file_path.exists():
        return False
    
    # Get file's creation time
    file_ctime = file_path.stat().st_ctime
    
    # Calculate newness threshold (3 minutes)
    threshold = time.time() - (minutes * 60)
    
    # Return True if file is newer than the threshold
    return file_ctime > threshold

def purge_old_files(hours=24, dry_run=False):
    """Remove files older than specified hours from the _recent bucket."""
    if not RECENT_DIR.exists():
        log(f"Recent directory {RECENT_DIR} does not exist, creating it")
        RECENT_DIR.mkdir(parents=True, exist_ok=True)
        save_sequence({"order": [], "favorites": []})
        return
    
    # Load sequence.json
    sequence = load_sequence()
    favorites = set(sequence.get("favorites", []))
    
    # Track changes
    removed_files = []
    
    # Scan files in the recent directory
    for file_path in RECENT_DIR.iterdir():
        # Skip directories, sequence.json itself, and any non-media files
        if (file_path.is_dir() or 
            file_path.name == "sequence.json" or 
            file_path.suffix.lower() not in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"]):
            continue
        
        # Skip files that are too new (might be in process of being written)
        if is_file_too_new(file_path):
            log(f"Skipping {file_path.name} - too new (less than 3 minutes old)")
            continue
        
        # Skip favorited files (future-proofing)
        if file_path.name in favorites:
            log(f"Skipping {file_path.name} - favorited")
            continue
        
        # Check if file is old enough to delete
        if is_file_older_than(file_path, hours):
            log(f"{'Would remove' if dry_run else 'Removing'} {file_path.name}")
            
            if not dry_run:
                # Delete the file
                file_path.unlink(missing_ok=True)
                
                # Also delete associated sidecar if it exists
                sidecar = file_path.with_suffix(file_path.suffix + ".json")
                if sidecar.exists():
                    sidecar.unlink()
                
                # Delete thumbnail if it exists
                thumb_dir = RECENT_DIR / "thumbnails"
                if thumb_dir.exists():
                    thumb_file = thumb_dir / f"{file_path.stem}{file_path.suffix}.jpg"
                    if thumb_file.exists():
                        thumb_file.unlink()
            
            removed_files.append(file_path.name)
    
    # Update sequence.json if files were removed
    if removed_files and not dry_run:
        # Remove deleted files from order array
        sequence["order"] = [
            item for item in sequence.get("order", [])
            if isinstance(item, dict) and item.get("file") not in removed_files
        ]
        save_sequence(sequence)
    
    # Report results
    if removed_files:
        log(f"{'Would remove' if dry_run else 'Removed'} {len(removed_files)} files older than {hours} hours")
    else:
        log(f"No files found older than {hours} hours")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge old files from the _recent bucket")
    parser.add_argument("--hours", type=int, default=24, help="Remove files older than N hours (default: 24)")
    parser.add_argument("--dry-run", action="store_true", help="Print files that would be removed without deleting them")
    args = parser.parse_args()
    
    log(f"Starting purge of files older than {args.hours} hours from {RECENT_BUCKET} bucket")
    
    try:
        purge_old_files(hours=args.hours, dry_run=args.dry_run)
        log("Purge completed successfully")
    except Exception as e:
        log(f"Error during purge: {str(e)}")
        sys.exit(1) 