#!/usr/bin/env python3
import json
import os
import glob
import sys

def update_bucket_json(file_path):
    print(f"Processing {file_path}")
    
    # Create backup
    backup_path = file_path + ".bak"
    os.system(f"cp '{file_path}' '{backup_path}'")
    
    # Read the data
    with open(file_path, 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON in {file_path}: {e}")
            return
    
    # Check if sequence exists and update it
    if 'sequence' in data:
        original_sequence = data['sequence']
        # Convert all string items to dictionary format
        data['sequence'] = [
            {'file': item} if not isinstance(item, dict) else item 
            for item in data['sequence']
        ]
        
        # Count changes
        changes = len([item for item in original_sequence if not isinstance(item, dict)])
        print(f"  - Updated {changes} items out of {len(original_sequence)}")
        
        # Write the updated data back
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  - Saved with {len(data['sequence'])} sequence items")
    else:
        print(f"  - No sequence found in {file_path}")

# Find all bucket.json files
bucket_files = glob.glob("output/**/bucket.json", recursive=True)

if not bucket_files:
    print("No bucket.json files found!")
    sys.exit(1)

print(f"Found {len(bucket_files)} bucket.json files")

# Update each file
for file_path in bucket_files:
    update_bucket_json(file_path)

print("All bucket.json files updated successfully!") 