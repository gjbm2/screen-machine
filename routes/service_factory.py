"""
Service factory that provides either real or mock implementations of services.
The choice between real and mock is controlled via the TESTING environment variable.
"""

import os
from typing import Union

def is_testing_mode() -> bool:
    """Determine if we're in testing mode based on environment variables."""
    return os.environ.get('TESTING', '').lower() == 'true'

def get_generation_service():
    """
    Get the appropriate generation service.
    
    Returns:
        A service that implements the generation interface
    """
    if is_testing_mode():
        from routes.mock.generation_service import MockGenerationService
        return MockGenerationService()
    else:
        # In production, we use the real implementation
        # For now we just return a function object that can be called directly
        from routes.alexa import handle_image_generation
        return handle_image_generation

def get_animation_service():
    """
    Get the appropriate animation service.
    
    Returns:
        A service that implements the animation interface
    """
    if is_testing_mode():
        from routes.mock.animation_service import MockAnimationService
        return MockAnimationService()
    else:
        # In production, we use the real implementation
        # For now we just return a function object that can be called directly
        from routes.alexa import async_amimate
        return async_amimate 

def get_display_service():
    """
    Get the appropriate display service.
    
    Returns:
        A function that implements the display_from_bucket interface
    """
    if is_testing_mode():
        from routes.mock.display_service import MockDisplayService
        # Create a singleton instance
        if not hasattr(get_display_service, '_instance'):
            get_display_service._instance = MockDisplayService()
        return get_display_service._instance.display_from_bucket
    else:
        # In production, use the real implementation
        from routes.publisher import display_from_bucket
        return display_from_bucket 