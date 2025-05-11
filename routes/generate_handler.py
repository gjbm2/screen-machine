"""
Generate handler - Utilities for generating content from other content
"""
import os
from pathlib import Path
import tempfile
import cv2
import logging

from flask import send_file
from utils.logger import info, error, warning, debug

def jpg_from_mp4_handler(mp4_path):
    """
    Extract the first frame from an MP4 file and return it as a JPG.
    
    Args:
        mp4_path: Path to the MP4 file
        
    Returns:
        Flask response with the JPG image
    """
    temp_path = None
    try:
        debug(f"Extracting first frame from: {mp4_path}")
        
        # Convert string path to Path object if needed
        if isinstance(mp4_path, str):
            mp4_path = Path(mp4_path)
            
        # Check if the file exists
        if not mp4_path.exists():
            error(f"MP4 file not found: {mp4_path}")
            return "File not found", 404, {"Content-Type": "text/plain"}
            
        # Open the video file
        video = cv2.VideoCapture(str(mp4_path))
        
        # Check if video opened successfully
        if not video.isOpened():
            error(f"Failed to open MP4 file: {mp4_path}")
            return "Failed to open video file", 500, {"Content-Type": "text/plain"}
        
        # Read the first frame
        success, frame = video.read()
        if not success:
            error(f"Failed to read frame from MP4: {mp4_path}")
            return "Failed to read frame from video", 500, {"Content-Type": "text/plain"}
        
        # Release the video file
        video.release()
        
        # No need to convert BGR to RGB since cv2.imwrite expects BGR
        # We'll just use the frame as is
        
        # Create a temporary file for the JPG
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
            temp_path = temp_file.name
            
        # Save the frame as a JPG
        cv2.imwrite(temp_path, frame)
        
        # Return the JPG file
        debug(f"Successfully extracted frame to: {temp_path}")
        return send_file(
            temp_path, 
            mimetype='image/jpeg', 
            as_attachment=False,
            # Add custom cleanup callback to delete the temp file 
            # after response is sent
            download_name=f"frame_{os.path.basename(mp4_path)}.jpg"
        )
        
    except Exception as e:
        # Clean up the temp file if there was an error
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass  # Ignore errors in cleanup
        error(f"Error extracting frame from MP4: {e}")
        return f"Error extracting frame: {e}", 500, {"Content-Type": "text/plain"}
    
    # The temp file cleanup is handled by the endpoint function 