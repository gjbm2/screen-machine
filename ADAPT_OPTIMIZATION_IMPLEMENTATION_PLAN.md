# Adapt Optimization Implementation Plan

## Implementation Steps

### 1. **Add Runtime Workflow Mutation System to generate.py**

#### 1.1 Add mutation functions to `routes/generate.py`
Add these functions before the `start()` function:

```python
def mutate_workflow_for_node_skip(workflow_data, workflow_config, args_namespace):
    """
    Mutate workflow at runtime to skip nodes based on node_skip parameters.
    
    Args:
        workflow_data: Loaded workflow JSON
        workflow_config: Workflow configuration from workflows.json
        args_namespace: Parsed arguments containing parameter values
    
    Returns:
        Modified workflow_data with mutations applied
    """
    from utils.logger import info, debug, warning
    import copy
    
    # Get parameters with node_skip: true
    skip_params = {}
    for param in workflow_config.get("params", []):
        if param.get("node_skip") == True:
            param_id = param["id"]
            param_value = getattr(args_namespace, param_id, param.get("default", True))
            skip_params[param_id] = param_value
            debug(f"Node skip parameter {param_id} = {param_value}")
    
    if not skip_params:
        return workflow_data
    
    # Make a deep copy to avoid modifying original
    workflow_data = copy.deepcopy(workflow_data)
    
    # Find nodes to skip based on parameter flags
    nodes_to_skip = []
    for node_id, node_def in workflow_data.items():
        node_title = node_def.get("_meta", {}).get("title", "")
        
        # Check if this node should be skipped
        for param_id, param_value in skip_params.items():
            flag_pattern = f"{{{{{param_id}}}}}"
            if flag_pattern in node_title and not param_value:
                nodes_to_skip.append(node_id)
                info(f"Marking node {node_id} ({node_title}) for skipping due to {param_id}=False")
                break
    
    if not nodes_to_skip:
        return workflow_data
    
    # Build dependency graph
    node_dependencies = {}  # node_id -> [(dependent_node, input_key)]
    node_inputs = {}        # node_id -> {input_key: (source_node, output_index)}
    
    for node_id, node_def in workflow_data.items():
        node_inputs[node_id] = {}
        for input_key, input_value in node_def.get("inputs", {}).items():
            if isinstance(input_value, list) and len(input_value) >= 2:
                source_node = str(input_value[0])
                output_index = input_value[1]
                node_inputs[node_id][input_key] = (source_node, output_index)
                
                # Track dependencies
                if source_node not in node_dependencies:
                    node_dependencies[source_node] = []
                node_dependencies[source_node].append((node_id, input_key))
    
    # Reroute connections for each skipped node
    for skip_node in nodes_to_skip:
        info(f"Rerouting connections for skipped node {skip_node}")
        
        # Find what depends on this node
        dependent_info = node_dependencies.get(skip_node, [])
        
        # Get the inputs of the skipped node
        skip_node_inputs = node_inputs.get(skip_node, {})
        
        # For each dependent, reroute to the first input of the skipped node
        for dependent_node, input_key in dependent_info:
            if dependent_node in nodes_to_skip:
                continue  # Skip if dependent is also being skipped
            
            # Find the first input of the skipped node to use as replacement
            if skip_node_inputs:
                # Use the first input's source
                first_input_key = list(skip_node_inputs.keys())[0]
                source_node, source_output = skip_node_inputs[first_input_key]
                
                # Update the connection
                workflow_data[dependent_node]["inputs"][input_key] = [source_node, source_output]
                info(f"Rerouted {dependent_node}.{input_key} from {skip_node} to {source_node}")
            else:
                warning(f"Could not find input for skipped node {skip_node}")
                # Remove the problematic input to prevent errors
                del workflow_data[dependent_node]["inputs"][input_key]
                info(f"Removed problematic input {input_key} from {dependent_node}")
    
    # Remove skipped nodes from workflow
    for skip_node in nodes_to_skip:
        if skip_node in workflow_data:
            del workflow_data[skip_node]
            info(f"Removed node {skip_node} from workflow")
    
    return workflow_data
```

#### 1.2 Integrate mutation into `start()` function
In `routes/generate.py`, modify the `start()` function after loading the workflow:

```python
# Find this section in start() function:
info(f"Trying to load: {args_namespace.workflow}")
with open(findfile(args_namespace.workflow), "r") as file:
    workflow_data = json.load(file)
info(f"Successfully loaded: {args_namespace.workflow}")

# Add this line immediately after:
workflow_data = mutate_workflow_for_node_skip(workflow_data, master_workflow_meta, args_namespace)
```

### 2. **Update workflows.json Configuration**

#### 2.1 Add upscale-flag parameter to flux_1_kontext_dev_1pic-l.json
In `src/data/workflows.json`, find the `flux_1_kontext_dev_1pic-l.json` entry and add this parameter:

```json
{
  "id": "upscale-flag",
  "node_skip": true,
  "name": "Perform upscale?",
  "type": "bool",
  "default": true
}
```

### 3. **Update Alexa Integration**

#### 3.1 Modify async_adapt function in `routes/generate_handler.py`
Replace the existing `async_adapt` function with:

```python
def async_adapt(targets, obj = {}, from_alexa=False):
    """
    Process adaptation for the specified targets.
    For adapt, each target needs its own image retrieved from that specific target.
    
    Args:
        targets: List of target IDs or single target ID
        obj: Optional dictionary with additional configuration
        from_alexa: Boolean indicating if called from Alexa (enables fast mode)
        
    Returns:
        None
    """
    # Import necessary modules
    from utils.logger import info, error, warning, debug
    from routes.utils import get_image_from_target, resolve_runtime_value
    import threading
    
    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Set the adapt refiner
    result.setdefault("data", {})["refiner"] = "adapt"
    
    # Handle multiple targets - each needs its own image
    if targets:
        info(f"Adapt: processing {len(targets)} targets: {targets}")
        
        # Process each target separately with its own image retrieval and generation
        for target_destination in targets:
            info(f"Adapt: retrieving current image from {target_destination}")
            image_payload = get_image_from_target(target_destination)
            
            # Create a separate request for each target
            target_result = {
                "intent": "adapt",
                "data": {
                    "prompt": result.get("data", {}).get("prompt", ""),
                    "refiner": "adapt",
                    "targets": [target_destination],  # Single target for this request
                    "workflow": result.get("data", {}).get("workflow")
                }
            }
            
            if image_payload:
                # Include the current published image as input for adaptation
                target_result["data"]["images"] = [image_payload]
                info(f"Adapt: found image for {target_destination}, length: {len(image_payload.get('image', ''))}")
            else:
                warning(f"Adapt: no current image found for {target_destination}")
                # Continue anyway - the adapt refiner can handle this case
            
            # Add upscale-flag parameter when called from Alexa
            call_kwargs = {}
            if from_alexa:
                call_kwargs["upscale-flag"] = False  # Skip upscale for fast adapt
                info(f"Adapt: Fast mode enabled for Alexa - skipping upscale")
            
            # Run the refinement + generation flow in background for this target
            threading.Thread(
                target=handle_image_generation,
                kwargs={
                    "input_obj": target_result,
                    **call_kwargs
                }
            ).start()
    else:
        warning("Adapt: no targets specified")
    
    return None
```

#### 3.2 Update Alexa process function in `routes/alexa.py`
Find the "adapt" case in the `process()` function and modify it:

```python
case "adapt":
    # Fetch relevant image inputs and process adaptation
    targets = result.get("data", {}).get("targets", []) if isinstance(result.get("data", {}).get("targets"), list) else []
    if not targets:
        # Default to closest screen if no targets specified
        targets = [closest_screen] if closest_screen else []
    # Expand groups to individual destinations
    expanded_targets = expand_alexa_targets_to_destinations(targets) if targets else []
    
    # Pass from_alexa=True to enable fast mode
    async_adapt(targets=expanded_targets, obj=result, from_alexa=True)
    
    # Throw user_interacting event for adaptation on all targets
    for target in expanded_targets:
        throw_user_interacting_event(target, action_type="generate")
```

### 4. **Add Auto-Upscale Event System**

#### 4.1 Add event throwing function to `routes/publisher.py`
Add this function at the top of the file:

```python
def throw_auto_upscale_event(publish_destination_id: str, wait_time: str = "2m"):
    """Throw an auto-upscale event that will upscale the current image after a delay"""
    from routes.scheduler_api import throw_event
    
    throw_event(
        event_key="_auto_upscale",
        scope=publish_destination_id,
        payload={
            "action": "adapt",
            "wait": wait_time
        },
        display_name="Auto-upscale after adapt",
        ttl="10m"  # Event expires if not consumed within 10 minutes
    )
```

#### 4.2 Modify `_record_publish` function in `routes/publisher.py`
Add this logic to the `_record_publish` function after the existing metadata recording:

```python
# Check if this was a fast adapt from Alexa (no upscale performed)
# Look for source_metadata in the function parameters/context
if (hasattr(args_namespace, 'upscale-flag') and 
    getattr(args_namespace, 'upscale-flag', True) == False and
    source_metadata and 
    source_metadata.get("workflow") == "flux_1_kontext_dev_1pic-l.json"):
    
    # Throw auto-upscale event
    throw_auto_upscale_event(bucket, wait_time="2m")
    info(f"[auto_upscale] Scheduled upscale for {bucket} in 2 minutes (fast adapt)")
```

### 5. **Add _current_image Variable Resolution**

#### 5.1 Update `process_jinja_template` in `routes/scheduler_utils.py`
Add this code to the `process_jinja_template` function before the template rendering:

```python
# Add special variable resolution for _current_image
if "_current_image" not in template_vars and publish_destination:
    try:
        from routes.publisher import get_published_info
        published_info = get_published_info(publish_destination)
        
        if published_info and published_info.get("published"):
            # Get the image path - prefer raw_url, fallback to constructed path
            image_path = published_info.get("raw_url")
            if not image_path:
                from routes.bucketer import bucket_path
                filename = published_info.get("published")
                if filename:
                    image_path = str(bucket_path(publish_destination) / filename)
            
            if image_path:
                template_vars["_current_image"] = image_path
                
    except Exception as e:
        # Gracefully handle errors - _current_image will be undefined
        pass
```

### 6. **Add Auto-Upscale Event Triggers to Schedulers**

#### 6.1 Add to each scheduler JSON file
Add this event trigger to each scheduler file (`north-screen.json`, `south-screen.json`, `devtest.json`, `the-beast.json`):

```json
{
  "type": "event",
  "value": "_auto_upscale",
  "trigger_actions": {
    "instructions_block": [
      {
        "action": "log",
        "message": "Auto-upscale triggered for {{ _event.payload.action | default('adapt') }} - waiting {{ _event.payload.wait | default('2m') }}"
      },
      {
        "action": "wait",
        "duration": "{{ _event.payload.wait | default('2m') }}"
      },
      {
        "action": "generate",
        "workflow": "upscale.json",
        "input": {
          "image": "{{ _current_image }}"
        },
        "publish": true
      }
    ],
    "urgent": true
  }
}
```

### 7. **Update Alexa Response Messages**

#### 7.1 Update response in `routes/alexa.py`
In the adapt case, update the response message to indicate the two-phase process:

```python
# After successful async_adapt call, update the response_ssml
if success_count == 1:
    response_ssml = Brianize("Adapted the image. I'll upscale it in a couple of minutes.")
else:
    response_ssml = Brianize(f"Adapted {success_count} images. I'll upscale them in a couple of minutes.")
```

### 8. **Testing and Validation**

#### 8.1 Test the mutation system
1. Call adapt from Alexa with `upscale-flag=False`
2. Verify upscale nodes are removed from workflow
3. Verify connections are properly rerouted
4. Verify fast generation occurs

#### 8.2 Test the auto-upscale system
1. Verify auto-upscale event is thrown after fast adapt
2. Verify event fires after 2-minute delay
3. Verify `_current_image` resolves correctly
4. Verify upscale workflow executes properly

#### 8.3 Test edge cases
1. Multiple rapid adapt commands
2. Undo during adapt cycle
3. No published image when upscale fires
4. Scheduler restart during wait period

## Implementation Notes

- The mutation system only handles simple node removal with basic rerouting
- Auto-upscale events replace previous ones (same event key)
- The `_current_image` variable gracefully handles missing images
- All changes are backward compatible with existing frontend usage
- Fast mode is only enabled when called from Alexa 