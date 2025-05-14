from routes.utils import (
    findfile, 
    resolve_runtime_value, 
    dict_substitute, 
    build_schema_subs, 
    get_image_from_target,
    find_destination_by_alexaclosest,
    get_qr,
    _load_json_once
)
from routes.scheduler_utils import get_exported_variables_with_values
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

# Sorts out prompt refinement and then calls out to generator, without updating webapp state
def handle_image_generation(input_obj, wait=False, **kwargs):
    # Re-import logging functions to ensure they're available in this scope
    from utils.logger import info, error, warning, debug
    
    subs = build_schema_subs()
       
    data = input_obj.get("data", {})
    prompt = data.get("prompt", None)
    refiner = data.get("refiner", "none")  
    workflow = data.get("workflow", None)
    images = data.get("images", [])
    batch_id = data.get("batch_id") or str(uuid.uuid4())  # Generate a batch_id if not provided

    # If no workflow specified, get default workflow from workflows.json
    if not workflow:
        workflows = _load_json_once("workflow", "workflows.json")
        default_workflow = next((w for w in workflows if w.get("default", False)), None)
        if not default_workflow:
            utils.logger.error("No default workflow found in workflows.json")
            return None
        workflow = default_workflow["id"]
        utils.logger.debug(f"Using default workflow: {workflow}")

    # Get "targets" from input data
    targets = data.get("targets", [])

    # If it's a string (possibly comma-separated), split and clean it
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]

    # Ensure it's a list at this point
    targets = targets if isinstance(targets, list) else [targets]

    # Final fallback if empty
    publish_targets = targets if targets else [None]

    if not prompt and not images:
        return None
    
    # Refine the prompt
    input_dict = {
        "prompt": prompt,
        "workflow": workflow
    }

    utils.logger.debug(f"****input_dict {input_dict}")
    
    corrected_refiner = resolve_runtime_value("refiner", refiner, return_key="system_prompt")
    
    # TODO: should be done with a proper lookup NOT runtimevalue -- which should ONLY be used to resolve informal strings
    images_required = resolve_runtime_value("refiner", refiner, return_key="uploadimages") or 0
    
    # Normalize input images list
    images = images or []
    prepared_images = []

    if images_required == 1:
        if len(images) >= 1:
            prepared_images = [images[0]]
    elif images_required == 2:
        if len(images) >= 2:
            prepared_images = [images[0], images[1]]
        elif len(images) == 1:
            prepared_images = [images[0], images[0]]  # reuse first image
    else:
        prepared_images = []  # none required
       
    utils.logger.info(f"> Using refiner: '{refiner}' -> '{corrected_refiner}'")
    
    # Refine if needed
    if corrected_refiner is not None:
        openai_args = {
            "user_prompt": json.dumps(input_dict),
            "system_prompt": dict_substitute(corrected_refiner, subs),
            "schema": json.loads(dict_substitute("refiner-enrich.schema.json.j2", subs))
        }
        
        if images_required:
            openai_args["images"] = prepared_images
            utils.logger.debug(f"[handle_image_generation] {len(prepared_images)} image(s) prepared for OpenAI prompt.")

        utils.logger.debug(f"[handle_image_generation] Calling openai_prompt with args:")
        utils.logger.debug(json.dumps(openai_args, indent=2)[:1000])  # print first 1000 chars to avoid base64 overflow

        
        refined_output = routes.openai.openai_prompt(**openai_args)
        refined_prompt = refined_output.get("full_prompt", prompt)
    else:
        refined_output = {}
        refined_prompt = prompt
        
    corrected_workflow = resolve_runtime_value(
        category="workflow", 
        input_value=refined_output.get("workflow", workflow),
        return_key="id", 
        match_key="id"
    )
    
    # Translate prompt into Chinese if required (for WAN)
    translate = data.get("translate") if "data" in locals() else False
    if translate: 
        openai_args = {
            "user_prompt": refined_prompt,
            "system_prompt": dict_substitute("prompt-translate.txt", subs)
        }
        final_prompt = routes.openai.openai_prompt(**openai_args)
        utils.logger.info(f"Translated: {refined_prompt} to {final_prompt}")
    else:
        final_prompt = refined_prompt

    #print(f"> input_dict: {input_dict} -> refined_output: {refined_output}")
    #print(f"> Workflow: {workflow} -> corrected_workflow: {corrected_workflow}")
    
    # We don't want to force publication if no target was specified
    no_targets = publish_targets == [None]

    # One thread per image
    threads=[]
    results = [None] * len(publish_targets)  # Pre-size results list
    safe_kwargs = kwargs.copy()
    safe_kwargs.pop("publish_destination", None)  # remove if exists, else no-op
    safe_kwargs["images"] = images
    # Remove batch_id from safe_kwargs since it's already in the data dict
    safe_kwargs.pop("batch_id", None)
    
    for idx, publish_destination in enumerate(publish_targets):
        if no_targets:
            corrected_publish_destination = None
        else:
            utils.logger.info(f"> For destination: {publish_destination}")
            corrected_publish_destination = resolve_runtime_value("destination", publish_destination)
            
        def thread_fn(index=idx, destination=corrected_publish_destination):
            result = routes.generate.start(
                prompt=final_prompt,
                workflow=corrected_workflow,
                publish_destination=destination,
                **safe_kwargs
            )
            results[index] = result  # Store result in shared list

        thread = threading.Thread(target=thread_fn)
        thread.start()
        threads.append(thread)

    utils.logger.info(f" * Spawned {len(threads)} generator threads.")

    # if we need to wait until the end, do so
    if wait:
        for t in threads:
            t.join()
        utils.logger.info(" * All generator threads completed.")
        return results
    else:
        return None

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
            case "get_variables":
                # Initialize global_vars with an empty dictionary
                global_vars = {}
                details = ""
                
                if closest_screen:
                    destinations = _load_json_once("destination", "publish-destinations.json")
                    screen_name = next(
                        (d["name"] for d in destinations if d["id"] == closest_screen),
                        closest_screen  # fallback if not found
                    )
                    duration = 60
                    details = f"I've put more details on {screen_name}."
                    friendly_names_with_values = get_exported_variables_with_values(closest_screen) 
                    routes.display.send_overlay(
                        html="overlay_variables.html.j2",
                        screens=[closest_screen],
                        substitutions={
                            'EXPORTED_FRIENDLY_NAMES_WITH_VALUES': friendly_names_with_values,
                            'SCREEN_NAME': screen_name,
                            'QR_BASE64': get_qr(publish=closest_screen),
                            'DURATION': duration
                        },
                        duration=duration * 1000,
                        clear=True
                    )
                else:
                    # Get global and group variables without a specific screen
                    try:
                        from routes.scheduler_utils import get_registry_summary
                        registry_summary = get_registry_summary()
                        global_vars = registry_summary.get("global", {}) if registry_summary else {}
                        utils.logger.info(f"Found {len(global_vars)} global variables in registry.")
                    except Exception as e:
                        utils.logger.error(f"Error getting registry summary: {e}")
                        global_vars = {}
                        registry_summary = {}
                    
                    # Find default screen to display on
                    default_screens = subs["ALEXA_DEFAULT_SCREENS"]
                    utils.logger.info(f"Default screens from subs: {default_screens}")
                    if default_screens:
                        target_screen = default_screens[0]
                        destinations = _load_json_once("destination", "publish-destinations.json")
                        screen_name = next(
                            (d["name"] for d in destinations if d["id"] == target_screen),
                            target_screen  # fallback if not found
                        )
                        duration = 60
                        details = f"I've put global variables on {screen_name}."
                        
                        routes.display.send_overlay(
                            html="overlay_variables.html.j2",
                            screens=[target_screen],
                            substitutions={
                                'EXPORTED_FRIENDLY_NAMES_WITH_VALUES': global_vars,
                                'SCREEN_NAME': screen_name,
                                'QR_BASE64': get_qr(publish=target_screen),
                                'DURATION': duration
                            },
                            duration=duration * 1000,
                            clear=True
                        )
                    else:
                        details = "But I couldn't find a screen to display them on."
                    
                # Construct a safe response that handles edge cases
                var_count = len(global_vars) if global_vars else 0
                response_message = f"I've found {var_count} global variables."
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
                
                # Provide feedback - if we have a friendly name mapping for this variable, use it in the response
                friendly_name = var_name
                for actual_name, friendly in {v: k for k, v in subs["FRIENDLY_TO_VAR_NAMES"].items()}.items():
                    if actual_name == var_name:
                        friendly_name = friendly
                        break
                        
                if var_name and var_value is not None:
                    response_ssml = Brianize(f"I've set {friendly_name} to {var_value}.")
                else:
                    response_ssml = Brianize("I couldn't understand which variable to set.")
            # User wants to animate a screen
            case "animate": 
                # Fetch relevant image inputs
                targets = result.get("data", {}).get("targets", []) if isinstance(result.get("data", {}).get("targets"), list) else []
                async_amimate(targets = targets, obj = result)
    
            case "generate_image":
                # Ensure we're using the currently selected refiner
                result.setdefault("data", {})["refiner"] = current_app.config.get("REFINER", "Enrich")
                if not result.get("data", {}).get("prompt"):
                    result["data"]["prompt"] = f"{alexa_intent} {utterance}"        

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

def async_amimate(targets, obj = {}):
    # Re-import logging functions to ensure they're available in this scope
    from utils.logger import info, error, warning, debug
    
    # TODO: make this smarter; handle multiple targets, accept input prompts, refiners, etc.

    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Get target ID directly (no need for resolve_runtime_value to fuzzy match)
    target_image_file = targets[0] if targets and len(targets) > 0 else None

    # Create a result dictionary
    if target_image_file:
        # Inject base64 image into result["data"]["images"]
        image_payload = get_image_from_target(target_image_file)
        
        if image_payload:
            result.setdefault("data", {})["images"] = [image_payload]
            
            utils.logger.info(
                f"Will address: {target_image_file}, "
                f"image present: True, "
                f"image length: {len(image_payload.get('image'))}"
            )
        else:
            utils.logger.warning(f"No image found at ./output/{target_image_file}.jpg or ./output/{target_image_file}.mp4")
    else:
        utils.logger.warning("No target specified for animation")
    
    # Ensure we're using the currently selected refiner
    result.setdefault("data", {})["refiner"] = resolve_runtime_value("refiner", "animate")
    
    # Run the refinement + generation flow in background
    threading.Thread(
        target=handle_image_generation,
        kwargs={
            "input_obj": result
        }
    ).start()

    return None
