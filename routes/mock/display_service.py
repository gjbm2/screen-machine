"""
Mock implementation of the display service for testing purposes.
This allows testing the display instruction without affecting real displays.
"""

class MockDisplayService:
    """Mock implementation of the display service."""
    
    def __init__(self):
        """Initialize the mock display service with empty state."""
        self.displayed_items = {}  # Store display history by destination
        self.last_mode = {}  # Store last display mode by destination
        self.last_silent = {}  # Store last silent setting by destination
    
    def display_from_bucket(self, publish_destination_id, mode="Next", silent=False, **kwargs):
        """
        Mock implementation of display_from_bucket that doesn't affect real displays.
        
        Args:
            publish_destination_id (str): The destination ID to display to
            mode (str): The display mode ("Next", "Random", or "Blank")
            silent (bool): Whether to use silent mode
            **kwargs: Additional parameters
            
        Returns:
            dict: A mock result indicating success
        """
        # Store display attempt in history
        if publish_destination_id not in self.displayed_items:
            self.displayed_items[publish_destination_id] = []
            
        # Generate a mock item based on mode
        item = None
        if mode == "Blank":
            item = "blank_screen"
        elif mode == "Random":
            item = f"random_favorite_{len(self.displayed_items[publish_destination_id])}"
        else:  # Next
            item = f"next_favorite_{len(self.displayed_items[publish_destination_id])}"
            
        # Add to history
        self.displayed_items[publish_destination_id].append({
            "item": item,
            "mode": mode,
            "silent": silent,
            "timestamp": None  # Could add actual timestamp if needed
        })
        
        # Update last state
        self.last_mode[publish_destination_id] = mode
        self.last_silent[publish_destination_id] = silent
        
        # Return mock success result
        return {
            "success": True,
            "message": f"Displayed {mode.lower()} item: {item}",
            "item": item
        }
    
    def get_display_history(self, publish_destination_id):
        """
        Get the display history for a destination.
        
        Args:
            publish_destination_id (str): The destination ID
            
        Returns:
            list: The display history for this destination
        """
        return self.displayed_items.get(publish_destination_id, []) 