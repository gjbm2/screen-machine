#!/usr/bin/env python3
"""
Test script for the new overlay instruction type.
"""

import json
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from routes.scheduler_handlers import handle_overlay
from datetime import datetime

def test_overlay_instruction():
    """Test the overlay instruction handler."""
    
    # Test case 1: Basic overlay with text
    instruction1 = {
        "action": "overlay",
        "text": "This is a test message for the overlay instruction.",
        "duration": "5s"
    }
    
    # Test case 2: Overlay with just text (fallback)
    instruction2 = {
        "action": "overlay",
        "text": "Simple test message",
        "duration": 3000
    }
    
    # Mock context and output
    context = {"vars": {}}
    output = []
    now = datetime.now()
    publish_destination = "test-destination"
    
    print("Testing overlay instruction handler...")
    
    try:
        # Test case 1
        print("\nTest 1: Text overlay")
        result1 = handle_overlay(instruction1, context, now, output, publish_destination)
        print(f"Result: {result1}")
        print(f"Output: {output}")
        
        # Test case 2
        print("\nTest 2: Simple text overlay")
        result2 = handle_overlay(instruction2, context, now, output, publish_destination)
        print(f"Result: {result2}")
        print(f"Output: {output}")
        
        print("\n✅ Overlay instruction tests completed successfully!")
        
    except Exception as e:
        print(f"❌ Error testing overlay instruction: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_overlay_instruction()
    sys.exit(0 if success else 1) 