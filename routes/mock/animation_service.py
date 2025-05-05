"""
Mock implementation of the animation service for testing purposes.
This allows testing without calling expensive backend APIs.
"""

class MockAnimationService:
    """Mock implementation of the animation service."""
    
    def animate(self, targets=None, obj=None, **kwargs):
        """
        Mock implementation of animate function from routes.alexa.
        
        Args:
            targets (list, optional): Target destinations
            obj (dict, optional): Input object with animation parameters
            **kwargs: Additional parameters
            
        Returns:
            dict: A mock result indicating that animation was initiated
        """
        data = obj.get("data", {}) if obj else {}
        image_path = data.get("image_path") or kwargs.get("image_path")
        prompt = data.get("prompt") or kwargs.get("prompt")
        refiner = data.get("refiner") or kwargs.get("refiner", "animator")
        
        # For async operations, we still return a result but in a real system
        # this would be kicked off as a background task
        return {
            "success": True,
            "message": "Animation started",
            "animation_id": f"mock_async_anim_{image_path or 'unknown'}"
        } 