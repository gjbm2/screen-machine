from routes.utils import (
    findfile, 
    resolve_runtime_value, 
    dict_substitute, 
    build_schema_subs, 
    get_image_from_target,
    find_destination_by_alexaclosest,
    get_qr,
    _load_json_once,
    get_groups_for_destination,
    get_destinations_for_group
)
from routes.scheduler_utils import (
    get_exported_variables_with_values, 
    throw_event, 
    get_registry_summary, 
    get_events_for_destination, 
    get_next_scheduled_action,
    scheduler_schedule_stacks
)
from config import SCHEDULER_TICK_INTERVAL, SCHEDULER_TICK_BUFFER
# Import modules rather than functions to avoid circular imports
import routes.openai
import routes.generate
import routes.display
import utils.logger
from utils.logger import info, error, warning, debug
import utils
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from flask import current_app
# Import logger module rather than individual functions
import utils.logger 
from routes.manage_jobs import cancel_all_jobs
from typing import Dict, Any
import uuid
from routes.generate_handler import throw_user_interacting_event, handle_image_generation, async_amimate

def Brianize(text: str) -> str:
    # Already contains Brian *and* prosody — leave untouched
    if re.search(r"<voice[^>]+name=['\"]?Brian['\"]?.*?<prosody[^>]*pitch=['\"]?low", text, re.DOTALL):
        return text

    # Already contains Brian but no prosody — add prosody around voice content
    if re.search(r"<voice[^>]+name=['\"]?Brian['\"]?", text):
        return re.sub(
            r"(<voice[^>]+name=['\"]?Brian['\"]?>)(.*?)(</voice>)",
            r"\1<prosody rate='slow'>\2</prosody>\3",
            text,
            flags=re.DOTALL,
        )

    # Contains <speak> but no voice — insert Brian + prosody
    if "<speak>" in text and "</speak>" in text:
        return re.sub(
            r"<speak>(.*?)</speak>",
            r"<speak><voice name='Brian'><prosody rate='slow'>\1</prosody></voice></speak>",
            text,
            flags=re.DOTALL,
        )

    # Plain text — wrap in full SSML
    return f"<speak><voice name='Brian'><prosody rate='slow'>{text}</prosody></voice></speak>"

def display_variables_overlay(subs, closest_screen=None):
    """Display variables overlay on the closest screen or default screen.
    Returns (var_count, details) tuple with the count of variables and any display details."""
    try:
        from routes.scheduler_utils import get_registry_summary, get_events_for_destination, get_next_scheduled_action
        from routes.scheduler import scheduler_schedule_stacks
        registry_summary = get_registry_summary()
        global_vars = registry_summary.get("global", {}) if registry_summary else {}
        group_vars = registry_summary.get("groups", {}) if registry_summary else {}
        utils.logger.info(f"Found {len(global_vars)} global variables in registry.")
    except Exception as e:
        utils.logger.error(f"Error getting registry summary: {e}")
        global_vars = {}
        group_vars = {}
        registry_summary = {}
    
    details = ""
    target_screen = closest_screen
    
    if not target_screen and "ALEXA_DEFAULT_SCREENS" in subs:
        default_screens = subs["ALEXA_DEFAULT_SCREENS"]
        if default_screens:
            target_screen = default_screens[0]
    
    if target_screen:
        destinations = _load_json_once("destination", "publish-destinations.json")
        screen_name = next(
            (d["name"] for d in destinations if d["id"] == target_screen),
            target_screen  # fallback if not found
        )
        duration = 60
        details = f"I've put more details on {screen_name}."
        
        # Get next scheduled action
        next_action_info = None
        try:
            utils.logger.info(f"Checking next scheduled action for {target_screen}")
            if target_screen in scheduler_schedule_stacks:
                utils.logger.info(f"Found schedule stack for {target_screen}")
                if scheduler_schedule_stacks[target_screen]:
                    current_schedule = scheduler_schedule_stacks[target_screen][-1]
                    utils.logger.info(f"Current schedule for {target_screen}: {current_schedule.get('triggers', [])}")
                    next_action = get_next_scheduled_action(target_screen, current_schedule)
                    utils.logger.info(f"Next action result: {next_action}")
                    if next_action.get("has_next_action"):
                        next_action_info = {
                            "time": next_action["next_time"],
                            "description": next_action["description"],
                            "time_until": next_action["time_until_display"]
                        }
                        utils.logger.info(f"Found next action: {next_action_info}")
                else:
                    utils.logger.info(f"No active schedule found for {target_screen}")
            else:
                utils.logger.info(f"No schedule stack found for {target_screen}")
        except Exception as e:
            utils.logger.error(f"Error getting next action: {e}")
            next_action_info = None
            
        # Get event triggers from current schedule
        events_info = []
        try:
            utils.logger.info(f"Getting event triggers for {target_screen} and related scopes")
            triggers_by_scope = get_event_triggers_for_scope(target_screen)
            
            # Convert to a list format for the overlay
            for scope_name, triggers in triggers_by_scope.items():
                for trigger in triggers:
                    events_info.append({
                        "scope": scope_name,
                        "description": trigger["description"],
                        "event_key": trigger["event_key"],
                        "pub_dest": trigger["pub_dest"]
                    })
            utils.logger.info(f"Found {len(events_info)} event triggers across all scopes")
        except Exception as e:
            utils.logger.error(f"Error getting event triggers: {e}")
            events_info = None
        
        # Prepare substitutions with all sections
        substitutions = {
            'EXPORTED_FRIENDLY_NAMES_WITH_VALUES': {
                **global_vars,
                **{f"{group_name}/{var_name}": var_info 
                   for group_name, group_dict in group_vars.items()
                   for var_name, var_info in group_dict.items()}
            },
            'SCREEN_NAME': screen_name,
            'QR_BASE64': get_qr(publish=target_screen),
            'DURATION': duration
        }
        
        # Add next action if available
        if next_action_info:
            substitutions['NEXT_ACTION'] = next_action_info
            utils.logger.info(f"Added next action to substitutions: {next_action_info}")
            
        # Add available events if any
        if events_info:
            substitutions['AVAILABLE_EVENTS'] = events_info
            utils.logger.info(f"Added event triggers to substitutions: {events_info}")
                    
        routes.display.send_overlay(
            html="overlay_schedule.html.j2",
            screens=[target_screen],
            substitutions=substitutions,
            duration=duration * 1000,
            clear=True
        )
    else:
        details = "But I couldn't find a screen to display them on."
    
    var_count = len(global_vars) + sum(len(vars) for vars in group_vars.values())
    throw_user_interacting_event(closest_screen, action_type="info")
    return var_count, details

def get_all_destinations_for_group(scope: str) -> list:
    """
    Given a scope (destination ID, group name, or 'global'), return a list of destination IDs.
    Unlike get_destinations_for_group, this doesn't filter based on has_bucket.
    """
    destinations = _load_json_once("destination", "publish-destinations.json")

    # If scope is 'global', return all destinations
    if scope == "global":
        return [d["id"] for d in destinations]

    # If scope matches a destination ID, return it
    for d in destinations:
        if d["id"] == scope:
            return [scope]

    # Otherwise, treat as group name
    return [
        d["id"] for d in destinations
        if scope in d.get("groups", [])
    ]

def get_event_triggers_for_scope(target_screen: str) -> dict:
    """Get all available event triggers for a target screen and its related scopes."""
    triggers_by_scope = {}
    utils.logger.debug(f"Starting event trigger collection for {target_screen}")
    
    # First get all groups for this destination
    groups = get_groups_for_destination(target_screen)
    utils.logger.debug(f"Found groups for {target_screen}: {groups}")
    
    # Add the target screen's own scope
    all_destinations = {target_screen}
    
    # Add global scope
    all_destinations.add("global")
    
    # Get all destinations for each group
    for group in groups:
        group_destinations = get_all_destinations_for_group(group)  # Use our new function
        utils.logger.debug(f"Found destinations for group {group}: {group_destinations}")
        all_destinations.update(group_destinations)
    
    utils.logger.debug(f"Total destinations to check: {all_destinations}")
        
    # Now get event triggers for each destination
    for dest in all_destinations:
        utils.logger.debug(f"Checking destination {dest} for event triggers")
        # Skip if no schedule stack
        if dest not in scheduler_schedule_stacks:
            utils.logger.debug(f"No schedule stack found for {dest}")
            continue
        if not scheduler_schedule_stacks[dest]:
            utils.logger.debug(f"Empty schedule stack for {dest}")
            continue
            
        current_schedule = scheduler_schedule_stacks[dest][-1]
        utils.logger.debug(f"Found schedule for {dest}: {current_schedule.get('triggers', [])}")
        dest_triggers = []
        
        for trigger in current_schedule.get("triggers", []):
            if trigger.get("type") == "event" and "value" in trigger:
                event_key = trigger["value"]
                # Skip system events
                if event_key.startswith('_'):
                    utils.logger.debug(f"Skipping system event {event_key} for {dest}")
                    continue
                    
                trigger_actions = trigger.get("trigger_actions", {})
                description = trigger_actions.get("description", event_key)
                
                trigger_info = {
                    "description": description,
                    "event_key": event_key,
                    "pub_dest": dest
                }
                dest_triggers.append(trigger_info)
                utils.logger.debug(f"Added trigger for {dest}: {trigger_info}")
                
        if dest_triggers:
            # Use destination name if available, otherwise ID
            destinations = _load_json_once("destination", "publish-destinations.json")
            dest_name = next(
                (d["name"] for d in destinations if d["id"] == dest),
                dest
            )
            triggers_by_scope[dest_name] = dest_triggers
            utils.logger.debug(f"Added {len(dest_triggers)} triggers for scope {dest_name}")
            
    utils.logger.debug(f"Final triggers by scope: {triggers_by_scope}")
    return triggers_by_scope

def process(data):
    # Re-import logging functions to ensure they're available in this scope
    from utils.logger import info, error, warning, debug
    
    alexa_intent = data.get("request", {}).get("intent", {}).get("name", "unspecified")
    utterance = data.get("request", {}).get("intent", {}).get("slots", {}).get("utterance", {}).get("value", "unspecified")
    device_id = data.get("context", {}).get("System", {}).get("device", {}).get("deviceId", None)
    closest_screen = find_destination_by_alexaclosest(device_id)
    request_type = data.get("request", {}).get("type", "")

    utils.logger.info(f"========================================================")

    # == User just said 'Computer, use AI' 
    if request_type == "LaunchRequest":
        
        if closest_screen:
            destinations = _load_json_once("destination", "publish-destinations.json")
            screen_name = next(
                (d["name"] for d in destinations if d["id"] == closest_screen),
                closest_screen  # fallback if not found
            )
            duration = 60
            details = f"I've put more details on {screen_name}."
            routes.display.send_overlay(
                html="overlay_explain.html.j2",
                screens=[closest_screen],
                substitutions={
                    'SCREEN_NAME': screen_name,
                    'QR_BASE64': get_qr(publish=closest_screen),
                    'DURATION': duration
                },
                duration=duration * 1000,
                clear=True
            )
        else:
            details = "Or 'use A.I. to ask how big is the moon, and take it from there."
            
        throw_user_interacting_event(closest_screen, action_type="info")     
        response_ssml = Brianize(f"Hello. Try: 'use A.I. to paint a cat'. {details}")
    
    # == User said something specific like 'Computer, use AI to paint a cat' ==
    elif request_type == "IntentRequest":
        utils.logger.info(f"> Alexa intent: {alexa_intent}, utterance: {utterance}, location {closest_screen}")
        
        # Build the subs dictionary
        subs = build_schema_subs()
        subs["ALEXA_CLOSEST_SCREEN"] = (
            [closest_screen] if closest_screen else subs["ALEXA_DEFAULT_SCREENS"]
        )
        
        # Add exported variable friendly names for this screen's scope
        if closest_screen:
            # Get all exported variables for this destination
            exported_vars = get_exported_variables_with_values(closest_screen)
            
            # Extract friendly names and actual names from the exported variables
            exported_var_friendly_names = []
            exported_var_names = []
            friendly_to_var_names = {}  # Map friendly names to actual variable names
            
            for var_name, var_info in exported_vars.items():
                # Add actual variable name
                exported_var_names.append(var_name)
                
                # Add friendly name if available
                if "friendly_name" in var_info:
                    friendly_name = var_info["friendly_name"]
                    exported_var_friendly_names.append(friendly_name)
                    friendly_to_var_names[friendly_name] = var_name
            
            # Add to subs
            subs["EXPORTED_VAR_FRIENDLY_NAMES"] = exported_var_friendly_names
            subs["EXPORTED_VAR_NAMES"] = exported_var_names
            subs["EXPORTED_VARS"] = exported_vars  # Add complete dictionary for maximum flexibility
            subs["FRIENDLY_TO_VAR_NAMES"] = friendly_to_var_names  # Add mapping from friendly names to actual var names
            
            # Add scope mapping for events
            destinations = _load_json_once("destination", "publish-destinations.json")
            scope_mapping = {
                "everywhere": "global",  # Allow "everywhere" as friendly name for global scope
                "global": "global"
            }
            
            # Build set of all groups
            groups = set()
            for dest in destinations:
                groups.update(get_groups_for_destination(dest["id"]))
            
            # Add group names
            for group in groups:
                scope_mapping[group] = group
            
            # Add destination friendly names
            for dest in destinations:
                if "name" in dest:
                    scope_mapping[dest["name"]] = dest["id"]
            
            subs["SCOPE_NAMES"] = scope_mapping
            
            # Get available event triggers from current schedule
            if closest_screen:
                utils.logger.debug(f"Getting event triggers for closest screen: {closest_screen}")
                triggers_by_scope = get_event_triggers_for_scope(closest_screen)
                utils.logger.debug(f"Raw triggers by scope: {triggers_by_scope}")
                
                # Convert to a format suitable for the LLM
                available_triggers = []
                for scope_name, triggers in triggers_by_scope.items():
                    utils.logger.debug(f"Processing triggers for scope {scope_name}: {triggers}")
                    for trigger in triggers:
                        trigger_info = {
                            "scope": scope_name,
                            "description": trigger["description"],
                            "event_key": trigger["event_key"],
                            "pub_dest": trigger["pub_dest"]
                        }
                        available_triggers.append(trigger_info)
                        utils.logger.debug(f"Added trigger info: {trigger_info}")
                        
                subs["AVAILABLE_EVENTS"] = available_triggers
                utils.logger.debug(f"Final available triggers for LLM: {available_triggers}")
            
            utils.logger.debug(f"Added {len(exported_var_friendly_names)} exported var friendly names to subs")
            utils.logger.debug(f"Added {len(exported_var_names)} exported var names to subs")
            utils.logger.debug(f"Added mapping dictionary from friendly names to var names")
        else:
            subs["EXPORTED_VAR_FRIENDLY_NAMES"] = []
            subs["EXPORTED_VAR_NAMES"] = []
            subs["EXPORTED_VARS"] = {}
            subs["FRIENDLY_TO_VAR_NAMES"] = {}
        
        # Fall back to a default response
        result = None
        response_ssml = "<speak><voice name='Brian'><prosody rate='slow'>I would respond, if I had the will to live.</prosody></voice></speak>"

        # == Select system prompt based on Alexa intent ==
        use_system_prompt = None
        match alexa_intent:
            case "repeat":
                result = current_app.config.get("LASTRENDER", None)
                if not result:
                    return Brianize("Nothing to regenerate")
                response_ssml = Brianize("Coming right up.")
            # User wants to get variables
            case "schedule":
                var_count, details = display_variables_overlay(subs, closest_screen)
                response_message = f"I've found {var_count} variables."
                if details:
                    response_message += f" {details}"
                response_ssml = Brianize(response_message)
            case "select_refiner":
                use_system_prompt="alexa-refiner.txt"
            case "cancel":
                total_cancelled = cancel_all_jobs()
                if total_cancelled:
                    response_ssml = Brianize(f"{total_cancelled} jobs cancelled.")
                else:
                    response_ssml = Brianize(f"No running jobs.")
            case "set":
                # Setting global or group vars (e.g. theme, style, etc.)
                use_system_prompt="alexa-setvars.txt.j2"
            case "animate":
                use_system_prompt="alexa-animate.txt"
            case "trigger":
                use_system_prompt="alexa-trigger-events.txt.j2"
            case _:
                use_system_prompt="alexa-triage.txt"

        # == Use OpenAI to triage ==
        if use_system_prompt:   
            # Try to get a proper triage (allow OpenAI 8s)
            
            # Add DEBUG logging to trace what's happening
            processed_system_prompt = dict_substitute(use_system_prompt, subs)
            user_prompt_text = f"{alexa_intent} {utterance}"
            schema_json = json.loads(dict_substitute("marvin.schema.json.j2", subs))
            
            utils.logger.debug(f"Using system prompt template: {use_system_prompt}")
            utils.logger.debug(f"User prompt: {user_prompt_text}")
            utils.logger.debug(f"Schema: {json.dumps(schema_json)[:200]}...")
            utils.logger.debug(f"EXPORTED_VAR_FRIENDLY_NAMES: {subs.get('EXPORTED_VAR_FRIENDLY_NAMES', [])}")
            
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        routes.openai.openai_prompt,
                        user_prompt=user_prompt_text,
                        system_prompt=processed_system_prompt,
                        schema=schema_json
                    )
                    
                    # Add a try/except block to catch JSON parsing errors
                    try:
                        result = future.result(timeout=7)
                        utils.logger.debug(f"OpenAI raw response: {str(result)[:500]}...")
                    except json.JSONDecodeError as json_err:
                        utils.logger.error(f"JSON parse error in OpenAI response: {json_err}")
                        utils.logger.error(f"Error location: line {json_err.lineno}, column {json_err.colno}, char {json_err.pos}")
                        raw_result = future.result(timeout=7)  # Get the raw result
                        utils.logger.error(f"Raw problematic JSON: {str(raw_result)[:1000]}...")
                        raise
                    
                response_ssml = result.get("response_ssml", "No response received from pre-processor.")
                current_app.config["LASTRENDER"] = result
            except TimeoutError:
                utils.logger.warning("⚠️ Triage took too long. Returning fallback response.")
                return response_ssml
            except Exception as e:
                utils.logger.warning(f"⚠️ Triage failed: {str(e)}")
                return response_ssml
            
        intent = result.get("intent", "respond_only") if result else "respond_only"
        
        #info(f"> processed object: {result}")
        #info(f"> intent: {intent}; response_ssml: {response_ssml}")    

        # == After processing the intent, we can handle the result ==
        match intent:
            # User wants to set a exported variable
            case "set_variable":
                # Handle setting variables
                var_data = result.get("data", {})
                var_name = var_data.get("var_name")  # This is now the actual variable name, not the friendly name
                var_value = var_data.get("value")
                
                utils.logger.debug(f"Set variable intent data: var_name={var_name}, var_value={var_value}, type={type(var_value)}")
                
                if var_name and var_value is not None and closest_screen:
                    utils.logger.info(f"Setting variable '{var_name}' to '{var_value}' on screen '{closest_screen}'")
                    
                    # Import the utility functions
                    from routes.scheduler_utils import set_exported_variable, set_context_variable
                    
                    # Find the export name in the registry if this is an exported variable
                    export_name = None
                    for export_var, info in subs["EXPORTED_VARS"].items():
                        if export_var == var_name:
                            export_name = export_var
                            utils.logger.debug(f"Found exported variable: {export_name}")
                            break
                    
                    if export_name:
                        # This is an exported variable, set it through the registry
                        set_result = set_exported_variable(export_name, var_value)
                        utils.logger.info(f"Updated exported variable '{export_name}': {set_result['status']}")
                    else:
                        # This is a regular context variable for the current destination
                        set_result = set_context_variable(closest_screen, var_name, var_value)
                        utils.logger.info(f"Set context variable '{var_name}': {set_result['status']}")
                else:
                    utils.logger.warning(f"Failed to set variable: var_name={var_name}, var_value={var_value}, screen={closest_screen}")
                    response_ssml = Brianize("I couldn't understand which variable to set.")
                        
                if var_name and var_value is not None:
                    # Display updated variables after setting
                    var_count, overlay_details = display_variables_overlay(subs, closest_screen)
            # User wants to trigger an event
            case "trigger":
                event_data = result.get("data", {})
                event_key = event_data.get("event_key")
                scope = event_data.get("scope", closest_screen)  # Default to closest screen's pub_dest
                
                utils.logger.debug(f"Trigger event intent data: event_key={event_key}, scope={scope}")
                
                if event_key:
                    utils.logger.info(f"Triggering event '{event_key}' with scope {scope}")
                    
                    # Calculate wait time to ensure one full scheduler tick plus buffer
                    wait_time = f"1s"
                    
                    # First throw a very short user_interacting event to clear any existing wait conditions
                    utils.logger.info(f"Throwing clearing event for {scope}")
                    throw_user_interacting_event(scope, action_type="clear_wait", wait_time=wait_time)
                    
                    # Wait for at least one full scheduler tick plus buffer
                    import time
                    time.sleep(SCHEDULER_TICK_INTERVAL + SCHEDULER_TICK_BUFFER)
                    
                    # Now throw the actual requested event
                    utils.logger.info(f"Now throwing requested event '{event_key}' for {scope}")
                    throw_event(scope=scope, key=event_key)
                else:
                    response_ssml = Brianize("I couldn't understand which event to trigger.")
            # User wants to animate a screen
            case "animate": 
                # Fetch relevant image inputs
                targets = result.get("data", {}).get("targets", []) if isinstance(result.get("data", {}).get("targets"), list) else []
                async_amimate(targets = targets, obj = result)
            
                # Throw a user_interacting event for animation with 30m wait
                target_destinations = result.get("data", {}).get("targets", [])
                for dest in target_destinations:
                    throw_user_interacting_event(dest, action_type="animate")
                
    
            case "generate_image":
                # Ensure we're using the currently selected refiner
                result.setdefault("data", {})["refiner"] = current_app.config.get("REFINER", "Enrich")
                if not result.get("data", {}).get("prompt"):
                    result["data"]["prompt"] = f"{alexa_intent} {utterance}"        

                # Throw user_interacting event if we have targets
                targets = result.get("data", {}).get("targets", [])
                if isinstance(targets, list):
                    for target in targets:
                        throw_user_interacting_event(target, action_type="generate")
                elif closest_screen:
                    # Use closest screen if no targets specified
                    throw_user_interacting_event(closest_screen, action_type="generate")

                # Run the refinement + generation flow in background
                threading.Thread(
                    target=handle_image_generation,
                    kwargs={
                        "input_obj": result
                    }
                ).start()
            # User wants to change the refiner
            case "change_refiner":
                # Just switch to a new refiner
                refiner = result.get("data", {}).get("refiner", None)
                # Now match it to the json possibles
                new_refiner = resolve_runtime_value("refiner", refiner, "system_prompt")
                current_app.config["REFINER"] = new_refiner
                utils.logger.info(f"Heard {refiner} -> corrected refiner: {current_app.config.get("REFINER")}")
    else:
        response_ssml = Brianize("Sorry, I can't help with that.")
    
    return response_ssml


