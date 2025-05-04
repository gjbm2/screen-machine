"""
Mock implementation of the generation service for testing purposes.
This allows testing without calling expensive backend APIs.
"""

class MockGenerationService:
    """Mock implementation of the generation service."""
    
    def generate(self, prompt, refiner=None, workflow=None, images=None, targets=None, **kwargs):
        """
        Mock implementation that returns predictable results without calling real API.
        
        Args:
            prompt (str): The prompt for generation
            refiner (str, optional): Refiner to use
            workflow (str, optional): Workflow to use
            images (list, optional): Reference images
            targets (list, optional): Target destinations
            **kwargs: Additional parameters
            
        Returns:
            list: A list of generation results with predictable values
        """
        # Create safe filename from prompt
        safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:10])
        
        return [{
            "message": f"mock_image_{safe_prompt}.jpg",
            "file": f"mock_image_{safe_prompt}.jpg",
            "prompt": prompt,
            "negative_prompt": "mock negative prompt",
            "seed": 12345,
            "input": {
                "prompt": prompt,
                "refiner": refiner,
                "workflow": workflow or "default_workflow",
                "images": images or [],
                "targets": targets or []
            }
        }]
    
    def handle_image_generation(self, input_obj, wait=True, **kwargs):
        """
        Mock implementation of handle_image_generation from routes.alexa.
        
        Args:
            input_obj (dict): The input object containing generation parameters
            wait (bool, optional): Whether to wait for completion
            **kwargs: Additional parameters
            
        Returns:
            list: A list of generation results
        """
        data = input_obj.get("data", {})
        prompt = data.get("prompt", "")
        refiner = data.get("refiner")
        workflow = data.get("workflow")
        images = data.get("images", [])
        targets = data.get("targets", [])
        
        return self.generate(
            prompt=prompt, 
            refiner=refiner, 
            workflow=workflow,
            images=images,
            targets=targets,
            **kwargs
        ) 