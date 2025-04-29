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
import routes.openai
import routes.generate
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from flask import current_app
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from routes.manage_jobs import cancel_all_jobs
from typing import Dict, Any

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
    
    subs = build_schema_subs()
       
    data = input_obj.get("data", {})
    prompt = data.get("prompt", None)
    refiner = data.get("refiner", "none")  
    workflow = data.get("workflow", None)
    images = data.get("images", [])

    # If no workflow specified, get default workflow from workflows.json
    if not workflow:
        workflows = _load_json_once("workflow", "workflows.json")
        default_workflow = next((w for w in workflows if w.get("default", False)), None)
        if not default_workflow:
            error("No default workflow found in workflows.json")
            return None
        workflow = default_workflow["id"]
        debug(f"Using default workflow: {workflow}")

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

    debug(f"****input_dict {input_dict}")
    
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
       
    info(f"> Using refiner: '{refiner}' -> '{corrected_refiner}'")
    
    # Refine if needed
    if corrected_refiner is not None:
        openai_args = {
            "user_prompt": json.dumps(input_dict),
            "system_prompt": dict_substitute(corrected_refiner, subs),
            "schema": json.loads(dict_substitute("refiner-enrich.schema.json.j2", subs))
        }
        
        if images_required:
            openai_args["images"] = prepared_images
            debug(f"[handle_image_generation] {len(prepared_images)} image(s) prepared for OpenAI prompt.")

        debug(f"[handle_image_generation] Calling openai_prompt with args:")
        debug(json.dumps(openai_args, indent=2)[:1000])  # print first 1000 chars to avoid base64 overflow

        
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
        info(f"Translated: {refined_prompt} to {final_prompt}")
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
    
    for idx, publish_destination in enumerate(publish_targets):
        if no_targets:
            corrected_publish_destination = None
        else:
            info(f"> For destination: {publish_destination}")
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

    info(f" * Spawned {len(threads)} generator threads.")

    # if we need to wait until the end, do so
    if wait:
        for t in threads:
            t.join()
        info(" * All generator threads completed.")
        return results
    else:
        return None

def process(data):
    alexa_intent = data.get("request", {}).get("intent", {}).get("name", "unspecified")
    utterance = data.get("request", {}).get("intent", {}).get("slots", {}).get("utterance", {}).get("value", "unspecified")
    device_id = data.get("context", {}).get("System", {}).get("device", {}).get("deviceId", None)
    closest_screen = find_destination_by_alexaclosest(device_id)
    request_type = data.get("request", {}).get("type", "")

    info(f"========================================================")
    
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
            
        response_ssml = routes.alexa.Brianize(f"Hello. Try: 'use A.I. to paint a cat'. {details}")
    
    elif request_type == "IntentRequest":
        info(f"> Alexa intent: {alexa_intent}, utterance: {utterance}, location {closest_screen}")
        
        # Build the subs dictionary
        subs = build_schema_subs()
        subs["ALEXA_CLOSEST_SCREEN"] = (
            [closest_screen] if closest_screen else subs["ALEXA_DEFAULT_SCREENS"]
        )
        
        result = None
        response_ssml = "<speak><voice name='Brian'><prosody rate='slow'>I would respond, if I had the will to live.</prosody></voice></speak>"

        # Select system prompt based on Alexa intent
        use_system_prompt = None
        match alexa_intent:
            case "repeat":
                result = current_app.config.get("LASTRENDER", None)
                if not result:
                    return Brianize("Nothing to regenerate")
                response_ssml = Brianize("Coming right up.")
            case "select_refiner":
                use_system_prompt="alexa-refiner.txt"
            case "cancel":
                total_cancelled = cancel_all_jobs()
                if total_cancelled:
                    response_ssml = Brianize(f"{total_cancelled} jobs cancelled.")
                else:
                    response_ssml = Brianize(f"No running jobs.")
            case "animate":
                use_system_prompt="alexa-animate.txt"
            case _:
                use_system_prompt="alexa-triage.txt"

        if use_system_prompt:   # We need to use openai to respond
            # Try to get a proper triage (allow OpenAI 8s)
            
            #info(f"user prompt: {dict_substitute(use_system_prompt, subs)}")
            #info(f"system prompt: {dict_substitute(use_system_prompt, subs)}")
            #info(f"schema try: {dict_substitute("marvin.schema.json.j2", subs)}")
            #info(f"schema: {json.loads(dict_substitute("marvin.schema.json.j2", subs))}")

            
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        routes.openai.openai_prompt,
                        user_prompt=f"{alexa_intent} {utterance}",
                        system_prompt=dict_substitute(use_system_prompt, subs),
                        schema=json.loads(dict_substitute("marvin.schema.json.j2", subs))
                    )
                    result = future.result(timeout=7)
                    
                response_ssml = result.get("response_ssml", "No response received from pre-processor.")
                current_app.config["LASTRENDER"] = result
            except TimeoutError:
                warning("⚠️ Triage took too long. Returning fallback response.")
                return response_ssml
            except Exception as e:
                warning(f"⚠️ Triage failed: {e}")
                return response_ssml
            
        intent = result.get("intent", "respond_only") if result else "respond_only"
        
        #info(f"> processed object: {result}")
        #info(f"> intent: {intent}; response_ssml: {response_ssml}")    

        match intent:
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
            case "change_refiner":
                # Just switch to a new refiner
                refiner = result.get("data", {}).get("refiner", None)
                # Now match it to the json possibles
                new_refiner = resolve_runtime_value("refiner", refiner, "system_prompt")
                current_app.config["REFINER"] = new_refiner
                info(f"Heard {refiner} -> corrected refiner: {current_app.config.get("REFINER")}")
    else:
        response_ssml = routes.alexa.Brianize("Sorry, I can't help with that.")
    
    return response_ssml

def async_amimate(targets, obj = {}):
    # TODO: make this smarter; handle multiple targets, accept input prompts, refiners, etc.

    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Resolve the file associated with the first target
    target_image_file = resolve_runtime_value("destination", targets[0], return_key="file") if targets else None

    # Create a result dictionary

    # Inject base64 image into result["data"]["images"]
    image_payload = get_image_from_target(target_image_file) if target_image_file else None
    result.setdefault("data", {})["images"] = [image_payload]
    
    info(
        f"Will address: {target_image_file}, "
        f"image present: {image_payload is not None}, "
        f"image length: {len(image_payload.get('image')) if image_payload else 'N/A'}"
    )
    
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
