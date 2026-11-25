import pytest
import os
import json
from datetime import datetime
from routes.scheduler_utils import (
    load_vars_registry, save_vars_registry, 
    register_exported_var, register_imported_var,
    get_exported_variables_with_values,
    update_imported_variables,
    remove_exported_var, remove_imported_var
)

def test_load_vars_registry_creates_default_if_missing(temp_registry_file):
    """Test that load_vars_registry creates a default registry if none exists."""
    # Ensure registry file doesn't exist
    if os.path.exists(temp_registry_file):
        os.remove(temp_registry_file)
    
    # Load registry - should create default
    registry = load_vars_registry()
    
    # Verify structure
    assert "global" in registry
    assert "groups" in registry
    assert "imports" in registry
    assert "last_updated" in registry
    
    # Verify it was saved
    assert os.path.exists(temp_registry_file)

def test_save_vars_registry(temp_registry_file):
    """Test that save_vars_registry properly saves to disk."""
    test_registry = {
        "global": {"test_var": {"value": "test"}},
        "groups": {},
        "imports": {},
        "last_updated": "2023-01-01T00:00:00"
    }
    
    # Save the registry
    save_vars_registry(test_registry)
    
    # Load it back and verify
    with open(temp_registry_file, 'r') as f:
        saved_registry = json.load(f)
    
    assert "global" in saved_registry
    assert "test_var" in saved_registry["global"]
    assert saved_registry["global"]["test_var"]["value"] == "test"
    
    # last_updated should be updated
    assert saved_registry["last_updated"] != "2023-01-01T00:00:00"

def test_register_exported_var_global(temp_registry_file):
    """Test registering a variable to global scope."""
    # Setup
    var_name = "test_var"
    friendly_name = "Test Variable"
    scope = "global"
    dest_id = "test_dest"
    timestamp = datetime.now().isoformat()
    
    # Register the variable
    register_exported_var(var_name, friendly_name, scope, dest_id, timestamp)
    
    # Verify it was registered
    registry = load_vars_registry()
    
    assert var_name in registry["global"]
    assert registry["global"][var_name]["friendly_name"] == friendly_name
    assert registry["global"][var_name]["owner"] == dest_id
    assert registry["global"][var_name]["timestamp"] == timestamp

def test_register_exported_var_group(temp_registry_file):
    """Test registering a variable to a group scope."""
    # Setup
    var_name = "group_var"
    friendly_name = "Group Variable"
    scope = "test_group"
    dest_id = "test_dest"
    timestamp = datetime.now().isoformat()
    
    # Register the variable
    register_exported_var(var_name, friendly_name, scope, dest_id, timestamp)
    
    # Verify it was registered
    registry = load_vars_registry()
    
    assert scope in registry["groups"]
    assert var_name in registry["groups"][scope]
    assert registry["groups"][scope][var_name]["friendly_name"] == friendly_name
    assert registry["groups"][scope][var_name]["owner"] == dest_id
    assert registry["groups"][scope][var_name]["timestamp"] == timestamp

def test_register_imported_var(temp_registry_file):
    """Test registering an imported variable."""
    # Setup
    var_name = "source_var"
    imported_as = "imported_var"
    source_dest = "source_dest"
    importing_dest = "importing_dest"
    timestamp = datetime.now().isoformat()
    
    # Register the import
    register_imported_var(var_name, imported_as, source_dest, importing_dest, timestamp)
    
    # Verify it was registered
    registry = load_vars_registry()
    
    assert var_name in registry["imports"]
    assert importing_dest in registry["imports"][var_name]
    assert registry["imports"][var_name][importing_dest]["imported_as"] == imported_as
    assert registry["imports"][var_name][importing_dest]["source"] == source_dest
    assert registry["imports"][var_name][importing_dest]["timestamp"] == timestamp

def test_remove_exported_var(temp_registry_file):
    """Test removing an exported variable."""
    # Setup - register a variable first
    var_name = "to_remove"
    friendly_name = "Variable to Remove"
    scope = "global"
    dest_id = "test_dest"
    timestamp = datetime.now().isoformat()
    
    register_exported_var(var_name, friendly_name, scope, dest_id, timestamp)
    
    # Verify it exists
    registry = load_vars_registry()
    assert var_name in registry["global"]
    
    # Remove it
    removed = remove_exported_var(var_name, dest_id)
    
    # Verify it was removed
    assert removed is True
    registry = load_vars_registry()
    assert var_name not in registry["global"]

def test_remove_imported_var(temp_registry_file):
    """Test removing an imported variable."""
    # Setup - register an import first
    var_name = "import_to_remove"
    imported_as = "local_var"
    source_dest = "source_dest"
    importing_dest = "importing_dest"
    timestamp = datetime.now().isoformat()
    
    register_imported_var(var_name, imported_as, source_dest, importing_dest, timestamp)
    
    # Verify it exists
    registry = load_vars_registry()
    assert var_name in registry["imports"]
    assert importing_dest in registry["imports"][var_name]
    
    # Remove it
    removed = remove_imported_var(var_name, importing_dest)
    
    # Verify it was removed
    assert removed is True
    registry = load_vars_registry()
    
    # The var_name entry might still exist, but the importing_dest should be gone
    if var_name in registry["imports"]:
        assert importing_dest not in registry["imports"][var_name]

def test_update_imported_variables(temp_registry_file, clean_scheduler_state):
    """Test updating variables across contexts."""
    # Setup - create contexts with vars
    dest1 = "source_dest"
    dest2 = "importing_dest"
    var_name = "shared_var"
    
    # Create context stacks
    clean_scheduler_state["contexts"][dest1] = [{"vars": {var_name: "initial_value"}}]
    clean_scheduler_state["contexts"][dest2] = [{"vars": {}}]
    
    # Register export and import
    register_exported_var(
        var_name=var_name,
        friendly_name="Shared Variable",
        scope="global",
        publish_destination=dest1,
        timestamp=datetime.now().isoformat()
    )
    
    register_imported_var(
        var_name=var_name,
        imported_as=var_name,  # Same name
        source_dest_id=dest1,
        importing_dest_id=dest2,
        timestamp=datetime.now().isoformat()
    )
    
    # Update the imported variables
    updates = update_imported_variables(var_name, "new_value")
    
    # Verify updates
    assert dest2 in updates
    assert var_name in updates[dest2]
    assert clean_scheduler_state["contexts"][dest2][0]["vars"][var_name] == "new_value" 
def test_unreadable_registry_is_never_saved_over_the_file(temp_registry_file):
    """25 Nov: a torn read came back empty and the next import saved it, wiping the room's variables."""
    register_exported_var("a_theme", "A theme", "drawing-room", "cloud", datetime.now().isoformat())
    good = open(temp_registry_file).read()
    with open(temp_registry_file, "w") as f:
        f.write("")  # what a reader saw mid-write
    register_imported_var("a_theme", "a_theme", "group:drawing-room", "north-screen", datetime.now().isoformat())
    assert open(temp_registry_file).read() == ""  # left alone, not replaced by an empty registry
    with open(temp_registry_file, "w") as f:
        f.write(good)
    assert "a_theme" in load_vars_registry()["groups"]["drawing-room"]

def test_concurrent_imports_and_exports_lose_nothing(temp_registry_file):
    """Scheduler threads import and export at once: every entry survives, and no read sees a torn file."""
    import threading
    names = [f"v{i}" for i in range(40)]
    for n in names:
        register_exported_var(n, n, "drawing-room", "cloud", datetime.now().isoformat())
    bad_reads = []

    def importer(dest):
        for n in names:
            register_imported_var(n, n, "group:drawing-room", dest, datetime.now().isoformat())
            if load_vars_registry().get("_unreadable"):
                bad_reads.append(n)

    def exporter():
        for n in names:
            register_exported_var(n + "_x", n, "drawing-room", "cloud", datetime.now().isoformat())

    threads = [threading.Thread(target=importer, args=(d,)) for d in ("north-screen", "south-screen", "the-beast")]
    threads.append(threading.Thread(target=exporter))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    registry = load_vars_registry()
    assert not bad_reads
    assert set(registry["groups"]["drawing-room"]) == set(names) | {n + "_x" for n in names}
    assert all(set(registry["imports"][n]) == {"north-screen", "south-screen", "the-beast"} for n in names)
