from routes.utils import findfile, resolve_runtime_value
import routes.openai
import routes.generate
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from flask import current_app
from utils.logger import log_to_console, info, error, warning, debug, console_logs

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
       
    data = input_obj.get("data", {})
    prompt = data.get("prompt", None)
    refiner = data.get("refiner", "none")  
    workflow = data.get("workflow", None)
    images = data.get("images", [])

    # Get "targets" from input data
    targets = data.get("targets", [])

    # If it's a string (possibly comma-separated), split and clean it
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]

    # Ensure it's a list at this point
    targets = targets if isinstance(targets, list) else [targets]

    # Final fallback if empty
    publish_targets = targets if targets else [None]
    
    info(f"publish_targets {publish_targets}")
    
    if not prompt and not images:
        return 
    
    # Refine the prompt
    input_dict = {
        "prompt": prompt,
        "workflow": workflow
    }
    
    corrected_refiner = resolve_runtime_value("refiner", refiner, return_key="system_prompt")
    info(f"> Using refiner: '{refiner}' -> '{corrected_refiner}'")
    # Refine if needed
    if corrected_refiner is not None:
        refined_output = routes.openai.openai_prompt(
            user_prompt=json.dumps(input_dict),
            system_prompt=corrected_refiner,
            schema="refiner-enrich.schema.json"
        )
        refined_prompt = refined_output.get("full_prompt",prompt)
    else:
        refined_output = {}
        refined_prompt = prompt
        
    corrected_workflow = resolve_runtime_value(
        category="workflow", 
        input_value=refined_output.get("workflow", workflow),
        return_key="id", 
        match_key="id"
    )

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
                prompt=refined_prompt,
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
    
'''    1
    # Spool upone thread per image
    for publish_destination in publish_targets:
        info(f"> For destination: {publish_destination}")
        corrected_publish_destination = resolve_runtime_value("destination",publish_destination)
        thread = threading.Thread(
            target=routes.generate.start,
            kwargs={
                "prompt": refined_prompt,
                "workflow": corrected_workflow,
                "publish_destination": corrected_publish_destination,
                **kwargs
            }
        )
        thread.start()
        threads.append(thread)
        
    info(f" * Spawned {len(threads)} generator threads.")
    
    if wait:
        # join staements here
        # return the collection'''


def process(data):
    alexa_intent = data.get("request", {}).get("intent", {}).get("name", "unspecified")
    utterance = data.get("request", {}).get("intent", {}).get("slots", {}).get("utterance", {}).get("value", "unspecified")

    info(f"========================================================")
    info(f"> Alexa intent: {alexa_intent}\n> Utterance: {utterance}")
    
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
        case _:
            use_system_prompt="alexa-triage.txt"

    if use_system_prompt:   # We need to use openai to respond
        # Try to get a proper triage (allow OpenAI 8s)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    routes.openai.openai_prompt,
                    user_prompt=f"{alexa_intent} {utterance}",
                    system_prompt=use_system_prompt,
                    schema="marvin.schema.json"
                )
                result = future.result(timeout=7.5)
                
            response_ssml = result.get("response_ssml", "No response received from pre-processor.")
            current_app.config["LASTRENDER"] = result
        except TimeoutError:
            warning("⚠️ Triage took too long. Returning fallback response.")
            return response_ssml
        except Exception as e:
            warning(f"⚠️ Triage failed: {e}")
            return response_ssml
        
    intent = result.get("intent", "respond_only")   
    
    print(f"> processed object: {result}")
    print(f"> intent: {intent}; response_ssml: {response_ssml}")    

    match intent:
        case "generate_image":
            # Ensure we're using the currently selected refiner
            result.setdefault("data", {})["refiner"] = current_app.config.get("REFINER", "Enrich")
            if result.get("data", {}).get("prompt", None) == "": 
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
    
    return response_ssml
