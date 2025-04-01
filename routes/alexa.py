from routes.utils import findfile
from routes.openai import openai_prompt
import routes.generate
import json
import re
import threading
import difflib
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# Internal JSON cache
_json_cache = {}


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

def _load_json_once(name, filename):
    """Find and load a JSON file using findfile(), cache it by name."""
    if name not in _json_cache:
        path = findfile(filename)
        if not path:
            raise FileNotFoundError(f"Could not locate file: {filename}")
        with open(path, "r", encoding="utf-8") as f:
            _json_cache[name] = json.load(f)
    return _json_cache[name]

def fuzzy_match(name, candidates, key="name"):
    """Fuzzy match input name to item in candidate list by key."""
    if not name:
        return None
    names = [item[key] for item in candidates if key in item]
    match = difflib.get_close_matches(name.lower(), [n.lower() for n in names], n=1, cutoff=0.4)
    if match:
        for item in candidates:
            if key in item and item[key].lower() == match[0]:
                return item
    return None

def resolve_runtime_value(category, input_value, return_key="id", match_key="name"):
    """
    Resolve a single category (refiner, workflow, destination) to a specific field from the matched item.
    
    Parameters:
    - category: 'refiner' | 'workflow' | 'destination'
    - input_value: string to match
    - return_key: field to return from matched item (default: 'id')
    - match_key: field to match against (default: 'name')
    """
    mapping = {
        "refiner": "refiners.json",
        "workflow": "workflows.json",
        "destination": "publish-destinations.json"
    }
    if category not in mapping:
        raise ValueError(f"Unknown category: {category}")
    
    data = _load_json_once(category, mapping[category])
    match = fuzzy_match(input_value, data, key=match_key)
    
    if match and return_key in match:
        return match[return_key]
    return None

def reset_cache():
    """Clear the cached JSON data."""
    _json_cache.clear()

def modelquery(input_prompt, system_prompt=None,schema=None, model_name="gpt-4o"):
    
    print(f"**** prompt: {input_prompt}, system_prompt: {system_prompt}, schema: {schema}")
    
    response = openai_prompt(
        user_prompt=input_prompt,
        system_prompt=system_prompt,
        model_name=model_name,
        schema=schema
    )
    
    #print(f"*** response: {response}")

    if isinstance(response, str):
        cleaned = re.sub(r"^```(?:json)?|```$", "", response.strip(), flags=re.MULTILINE).strip()
        try:
            response = json.loads(cleaned)
        except Exception as e:
            print("!!! Failed to parse JSON response:")
            print(cleaned)
            raise
            
    return response
    
def handle_image_generation(result, alexa_intent, utterance):

    extract_data = result.get("data", {})
    prompt = extract_data.get("prompt", f"{alexa_intent} {utterance}")
    refiner = extract_data.get("refiner", "artify")        
    workflow = extract_data.get("workflow", None)
    publish_targets = extract_data.get("targets", [])
    
    # Refine the prompt
    input_dict = {
        "prompt": prompt,
        "workflow": workflow
    }
    corrected_refiner = resolve_runtime_value("refiner", refiner, "system_prompt")
    
    print(f"refiner: {refiner} -> corrected_refiner: {corrected_refiner}")
    
    refined_output = modelquery(
        input_prompt=json.dumps(input_dict),
        system_prompt=corrected_refiner,
        schema="refiner-enrich.schema.json"
    )
    
    refined_prompt = refined_output.get("full_prompt",prompt)
    corrected_workflow = resolve_runtime_value(
        category="workflow", 
        input_value=refined_output.get("workflow","sdxl"),
        return_key="id", 
        match_key="id"
    )

    print(f"input_dict: {input_dict} -> refined_output: {refined_output}")
    print(f"workflow: {workflow} -> corrected_workflow: {corrected_workflow}")
    
    # We were successful so create images...
    threads=[] 
    
    print(f"publish_targets: {publish_targets}")
    
    # Spool upone thread per image
    for target in publish_targets:
        corrected_target = resolve_runtime_value("destination",target)
        thread = threading.Thread(
            target=routes.generate.main,
            kwargs={
                "prompt": refined_prompt,
                "workflow": corrected_workflow,
                "target": corrected_target
            }
        )
        thread.start()
        threads.append(thread)
    
    print(f"Spawned {len(threads)} generator threads.")


def process(data):
    alexa_intent = data.get("request", {}).get("intent", {}).get("name", "unspecified")
    utterance = data.get("request", {}).get("intent", {}).get("slots", {}).get("utterance", {}).get("value", "unspecified")

    print(f"> Alexa intent: {alexa_intent}\n> Utterance: {utterance}")
    
    result = None
    response_ssml = "<speak><voice name='Brian'><prosody rate='slow'>I would respond, if I had the will to live.</prosody></voice></speak>"

    # Try to get a proper triage (allow OpenAI 5s)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                modelquery,
                input_prompt=f"{alexa_intent} {utterance}",
                system_prompt="alexa-triage.txt",
                schema="marvin.schema.json"
            )
            result = future.result(timeout=5)
    except TimeoutError:
        print("⚠️ Triage took too long. Returning fallback response.")
        return response_ssml
    except Exception as e:
        print(f"⚠️ Triage failed: {e}")
        return response_ssml
    
    print(f"> processed object: {result}")
    
    intent = result.get("intent", "respond_only")
    response_ssml = result.get("response_ssml", "No response received from pre-processor.")
    
    print(f"> intent: {intent}; response_ssml: {response_ssml}")
    
    if intent=="generate_image":
        # Run the refinement + generation flow in background
        threading.Thread(
            target=handle_image_generation,
            kwargs={"result": result, "alexa_intent": alexa_intent, "utterance": utterance}
        ).start()
    
    return response_ssml