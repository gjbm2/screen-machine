"""
Mock implementation of the animation service for testing purposes.
This allows testing without calling expensive backend APIs.
"""

class MockAnimationService:
    """Mock implementation of the animation service."""
    
    def animate(self, image_path=None, prompt=None, refiner=None, targets=None, **kwargs):
        """
        Mock implementation that returns predictable results without calling real API.
        
        Args:
            image_path (str, optional): Path to the image to animate
            prompt (str, optional): Prompt to guide animation
            refiner (str, optional): Refiner to use
            targets (list, optional): Target destinations
            **kwargs: Additional parameters
            
        Returns:
            dict: A result indicating success and mock animation data
        """
        # Create a deterministic identifier from inputs
        identifier = f"{image_path or 'unknown'}_{prompt or 'noprompt'}"[:20]
        safe_id = "".join(c if c.isalnum() else "_" for c in identifier)
        
        return {
            "success": True,
            "animation_id": f"mock_animation_{safe_id}",
            "file": f"mock_animation_{safe_id}.mp4",
            "original_image": image_path,
            "prompt": prompt,
            "refiner": refiner or "animator"
        }
    
    def async_amimate(self, targets=None, obj=None, **kwargs):
        """
        Mock implementation of async_amimate from routes.alexa.
        
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