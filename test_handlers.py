#!/usr/bin/env python3

from datetime import datetime
from routes.scheduler_handlers import handle_set_var
from routes.service_factory import get_display_service

def test_set_var_with_input_value():
    """Test the set_var handler with input.value format."""
    context = {"vars": {}}
    now = datetime.now()
    output = []
    
    instruction = {
        "var": "test_var",
        "input": {
            "value": "test_value"
        }
    }
    
    result = handle_set_var(instruction, context, now, output, "test_dest")
    print(f"Set var result: {result}")
    print(f"Context: {context}")
    print(f"Output: {output}")
    
    assert result is False
    assert "test_var" in context["vars"]
    assert context["vars"]["test_var"] == "test_value"
    assert len(output) == 1
    
    print("Test set_var with input.value passed!")

def test_get_service_factory():
    """Test the service factory."""
    display_service = get_display_service()
    print(f"Got display service: {display_service}")
    assert display_service is not None
    
    print("Test get_service_factory passed!")

if __name__ == "__main__":
    print("Running tests...")
    test_set_var_with_input_value()
    test_get_service_factory()
    print("All tests passed!") 