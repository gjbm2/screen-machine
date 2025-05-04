import pytest
from datetime import datetime
from routes.scheduler_handlers import handle_export_var, handle_import_var, handle_set_var
from routes.scheduler_utils import load_vars_registry

def test_export_var_handler(temp_registry_file, setup_test_destination, current_time):
    """Test the export variable handler function."""
    dest_id = setup_test_destination
    context = {"vars": {"test_var": "test_value"}}
    output = []
    
    # Create an export instruction
    instruction = {
        "var_name": "test_var",
        "friendly_name": "Test Variable",
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_export_var(instruction, context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False  # Should not unload
    assert len(output) == 1  # Should log something
    
    # Verify registry state
    registry = load_vars_registry()
    assert "test_var" in registry["global"]
    assert registry["global"]["test_var"]["friendly_name"] == "Test Variable"
    assert registry["global"]["test_var"]["owner"] == dest_id

def test_export_var_handler_nonexistent_var(temp_registry_file, setup_test_destination, current_time):
    """Test the export variable handler with a non-existent variable."""
    dest_id = setup_test_destination
    context = {"vars": {}}  # Empty vars
    output = []
    
    # Create an export instruction for a non-existent variable
    instruction = {
        "var_name": "missing_var",
        "friendly_name": "Missing Variable",
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_export_var(instruction, context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False  # Should not unload
    assert len(output) == 1  # Should log something
    assert "Failed to export" in output[0]  # Should log failure
    
    # Verify registry state - should not have the variable
    registry = load_vars_registry()
    assert "missing_var" not in registry["global"]

def test_export_var_handler_null_value(temp_registry_file, setup_test_destination, current_time):
    """Test the export variable handler with a null value (removal)."""
    dest_id = setup_test_destination
    
    # First set up a variable and export it
    context = {"vars": {"remove_var": "initial_value"}}
    
    # Export the variable
    export_instruction = {
        "var_name": "remove_var",
        "friendly_name": "Variable to Remove",
        "scope": "global"
    }
    handle_export_var(export_instruction, context, current_time, [], dest_id)
    
    # Verify it was exported
    registry = load_vars_registry()
    assert "remove_var" in registry["global"]
    
    # Now set it to None and export again
    context["vars"]["remove_var"] = None
    output = []
    
    # Should recognize this as a removal operation
    should_unload = handle_export_var(export_instruction, context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False
    assert len(output) == 1
    assert "Removed export" in output[0]
    
    # Verify registry state - should not have the variable anymore
    registry = load_vars_registry()
    assert "remove_var" not in registry["global"]

def test_import_var_handler(temp_registry_file, clean_scheduler_state, current_time):
    """Test the import variable handler function."""
    # Setup source and destination
    source_id = "source_dest"
    dest_id = "dest_id"
    
    # Create context stacks
    clean_scheduler_state["contexts"][source_id] = [{
        "vars": {"source_var": "source_value"}
    }]
    
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {}
    }]
    
    # First export the variable from source
    source_context = clean_scheduler_state["contexts"][source_id][0]
    export_instruction = {
        "var_name": "source_var",
        "friendly_name": "Source Variable",
        "scope": "global"
    }
    handle_export_var(export_instruction, source_context, current_time, [], source_id)
    
    # Now import it to destination
    dest_context = clean_scheduler_state["contexts"][dest_id][0]
    output = []
    
    import_instruction = {
        "var_name": "source_var",
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_import_var(import_instruction, dest_context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False
    assert len(output) == 1
    assert "source_value" in output[0]  # Should log the imported value
    
    # Verify the variable was imported to destination context
    assert "source_var" in dest_context["vars"]
    assert dest_context["vars"]["source_var"] == "source_value"
    
    # Verify registry state - should have the import relationship
    registry = load_vars_registry()
    assert "source_var" in registry["imports"]
    assert dest_id in registry["imports"]["source_var"]

def test_import_var_handler_with_rename(temp_registry_file, clean_scheduler_state, current_time):
    """Test the import variable handler with renaming the variable."""
    # Setup source and destination
    source_id = "source_dest"
    dest_id = "dest_id"
    
    # Create context stacks
    clean_scheduler_state["contexts"][source_id] = [{
        "vars": {"source_var": "source_value"}
    }]
    
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {}
    }]
    
    # First export the variable from source
    source_context = clean_scheduler_state["contexts"][source_id][0]
    export_instruction = {
        "var_name": "source_var",
        "friendly_name": "Source Variable",
        "scope": "global"
    }
    handle_export_var(export_instruction, source_context, current_time, [], source_id)
    
    # Now import it to destination with a different name
    dest_context = clean_scheduler_state["contexts"][dest_id][0]
    output = []
    
    import_instruction = {
        "var_name": "source_var",
        "as": "local_name",
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_import_var(import_instruction, dest_context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False
    
    # Verify the variable was imported to destination context with new name
    assert "local_name" in dest_context["vars"]
    assert dest_context["vars"]["local_name"] == "source_value"
    
    # Verify registry state - should have the import relationship with renamed field
    registry = load_vars_registry()
    assert dest_id in registry["imports"]["source_var"]
    assert registry["imports"]["source_var"][dest_id]["imported_as"] == "local_name"

def test_import_var_handler_nonexistent_var(temp_registry_file, setup_test_destination, current_time):
    """Test the import variable handler with a non-existent variable."""
    dest_id = setup_test_destination
    context = {"vars": {}}
    output = []
    
    # Import a variable that doesn't exist
    import_instruction = {
        "var_name": "missing_var",
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_import_var(import_instruction, context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False
    assert len(output) == 1
    assert "Failed to import" in output[0]
    
    # Verify the variable was not added to context
    assert "missing_var" not in context["vars"]

def test_import_var_handler_remove_import(temp_registry_file, clean_scheduler_state, current_time):
    """Test the import variable handler to remove an import."""
    # Setup source and destination
    source_id = "source_dest"
    dest_id = "dest_id"
    
    # Create context stacks
    clean_scheduler_state["contexts"][source_id] = [{
        "vars": {"source_var": "source_value"}
    }]
    
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {}
    }]
    
    # First export the variable from source
    source_context = clean_scheduler_state["contexts"][source_id][0]
    export_instruction = {
        "var_name": "source_var",
        "friendly_name": "Source Variable",
        "scope": "global"
    }
    handle_export_var(export_instruction, source_context, current_time, [], source_id)
    
    # Now import it to destination
    dest_context = clean_scheduler_state["contexts"][dest_id][0]
    import_instruction = {
        "var_name": "source_var",
        "scope": "global"
    }
    handle_import_var(import_instruction, dest_context, current_time, [], dest_id)
    
    # Verify it was imported
    registry = load_vars_registry()
    assert "source_var" in registry["imports"]
    assert dest_id in registry["imports"]["source_var"]
    
    # Now remove the import
    output = []
    remove_instruction = {
        "var_name": "source_var",
        "as": None,  # None means remove
        "scope": "global"
    }
    
    # Execute the handler
    should_unload = handle_import_var(remove_instruction, dest_context, current_time, output, dest_id)
    
    # Verify handler behavior
    assert should_unload is False
    assert len(output) == 1
    assert "Removed import" in output[0]
    
    # Verify registry state - the import should be removed
    registry = load_vars_registry()
    if "source_var" in registry["imports"]:
        assert dest_id not in registry["imports"]["source_var"]

def test_var_update_propagation(temp_registry_file, clean_scheduler_state, current_time):
    """Test that variable updates propagate correctly."""
    # Setup source and destination
    source_id = "source_dest"
    dest_id = "dest_id"
    
    # Create context stacks
    clean_scheduler_state["contexts"][source_id] = [{
        "vars": {"source_var": "initial_value"}
    }]
    
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {}
    }]
    
    # First export the variable from source
    source_context = clean_scheduler_state["contexts"][source_id][0]
    export_instruction = {
        "var_name": "source_var",
        "friendly_name": "Source Variable",
        "scope": "global"
    }
    handle_export_var(export_instruction, source_context, current_time, [], source_id)
    
    # Now import it to destination
    dest_context = clean_scheduler_state["contexts"][dest_id][0]
    import_instruction = {
        "var_name": "source_var",
        "scope": "global"
    }
    handle_import_var(import_instruction, dest_context, current_time, [], dest_id)
    
    # Verify initial import
    assert dest_context["vars"]["source_var"] == "initial_value"
    
    # Now update the variable in source
    set_instruction = {
        "var": "source_var",
        "input": {"value": "updated_value"}
    }
    handle_set_var(set_instruction, source_context, current_time, [], source_id)
    
    # The update should not automatically propagate (would need explicit call)
    # But we need to mock that call in a real system
    
    # Let's update our mock to simulate a fetch from the registry
    from routes.scheduler_utils import update_imported_variables
    update_imported_variables("source_var", "updated_value")
    
    # Verify that the value was updated in destination
    assert dest_context["vars"]["source_var"] == "updated_value" 