
import json
import os
import shutil

def ensure_directory(directory):
    """Ensure a directory exists, creating it if necessary."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def copy_data_files():
    """Copy data files from src/data to a location accessible by the Flask app."""
    # Ensure the data directory exists
    ensure_directory("data")
    
    # Source directory
    src_data_dir = "src/data"
    
    # List of data files to copy
    data_files = [
        "global-options.json",
        "intro-texts.json",
        "refiners.json",
        "refiner-params.json",
        "workflows.json",
        "example-prompts.json",
        "maintenance-links.json",
        "publish-destinations.json"
    ]
    
    # Copy each file
    for file_name in data_files:
        src_path = os.path.join(src_data_dir, file_name)
        dest_path = os.path.join("data", file_name)
        
        if os.path.exists(src_path):
            shutil.copy2(src_path, dest_path)
            print(f"Copied {file_name} to data directory")
        else:
            print(f"Warning: {file_name} not found in {src_data_dir}")

if __name__ == "__main__":
    copy_data_files()
