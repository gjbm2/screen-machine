import os
import time
import json
import textwrap
import hashlib
import re
from openai import OpenAI
from dotenv import load_dotenv
from routes.utils import findfile
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# Load environment variables from .env (if present)
load_dotenv()

# Caches
_system_prompt_cache = {}
_file_upload_cache = {}
_function_schema_cache = {}

'''
# Calls out to openai
def modelquery(input_prompt, system_prompt=None,schema=None, model_name="gpt-4o"):
    
    info(f"**** prompt: {input_prompt}, system_prompt: {system_prompt}, schema: {schema}")
    
    response = openai_prompt(
        user_prompt=input_prompt,
        system_prompt=system_prompt,
        model_name=model_name,
        schema=schema
    )
    
    #info(f"*** response: {response}")

    if isinstance(response, str):
        cleaned = re.sub(r"^```(?:json)?|```$", "", response.strip(), flags=re.MULTILINE).strip()
        try:
            response = json.loads(cleaned)
        except Exception as e:
            info("!!! Failed to parse JSON response:")
            info(cleaned)
            raise
            
    return response'''

def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def hash_schema(schema):
    return hashlib.md5(json.dumps(schema, sort_keys=True).encode()).hexdigest()

def openai_prompt(user_prompt, system_prompt=None, model_name="gpt-4o", upload=None, schema=None, verbose=True):
    # === Ensure OpenAI API key is loaded ===
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment. Check your .env file or environment variables.")
    openai_client = OpenAI(api_key=openai_key)

    # === Load or parse system prompt ===
    if system_prompt:
        if system_prompt in _system_prompt_cache:
            system_message = _system_prompt_cache[system_prompt]
        else:
            path = findfile(system_prompt)
            if path and os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    system_message = f.read()
                _system_prompt_cache[system_prompt] = system_message
            else:
                system_message = system_prompt
    else:
        system_message = ""

    # === Fast path: No upload, no schema ===
    if not upload and not schema:
        if verbose:
            info(f"[OpenAI.openai_prompt] > ChatCompletion (simple text):\n"
                  f"{textwrap.indent(textwrap.fill(user_prompt, width=80), '    ')}")

        messages = [{"role": "system", "content": system_message}] if system_message else []
        messages.append({"role": "user", "content": user_prompt})

        response = openai_client.chat.completions.create(
            model=model_name,
            messages=messages
        )

        result = response.choices[0].message.content.strip()

        # Clean output: strip ```json ... ``` if present
        cleaned = re.sub(r"^```(?:json)?|```$", "", result.strip(), flags=re.MULTILINE).strip()
        try:
            if verbose:
                info(f"[OpenAI.openai_prompt] > Output:\n"
                      f"{textwrap.indent(textwrap.fill(cleaned, width=80), '    ')}")
            return json.loads(cleaned)
        except Exception:
            return result  # Return raw string if not valid JSON

    # === File uploads ===
    uploaded_files = []
    if upload:
        if isinstance(upload, str):
            upload = [upload]
        for filepath in upload:
            resolved = findfile(filepath)
            if resolved and os.path.isfile(resolved):
                abs_path = os.path.abspath(resolved)
                file_hash = hash_file(abs_path)
                if file_hash in _file_upload_cache:
                    file_id = _file_upload_cache[file_hash]
                else:
                    with open(abs_path, "rb") as f:
                        uploaded = openai_client.files.create(file=f, purpose="assistants")
                        file_id = uploaded.id
                        _file_upload_cache[file_hash] = file_id
                uploaded_files.append(file_id)
                if verbose:
                    info(f"[SUCCESS] Cached or uploaded: {filepath} => {file_id}")
            else:
                if verbose:
                    info(f"[WARNING] File not found or invalid: {filepath}")

    # === Schema mode (Function calling) ===
    if schema and not upload:
        if isinstance(schema, str):
            if schema in _function_schema_cache:
                fn_def = _function_schema_cache[schema]
            else:
                resolved = findfile(schema)
                if not resolved or not os.path.isfile(resolved):
                    raise FileNotFoundError(f"Schema file not found: {schema}")
                with open(resolved, "r", encoding="utf-8") as f:
                    schema_data = json.load(f)
                if "parameters" in schema_data:
                    fn_def = schema_data
                    schema_hash = hash_schema(fn_def["parameters"])
                else:
                    schema_hash = hash_schema(schema_data)
                    fn_def = {
                        "name": f"auto_function_{schema_hash[:8]}",
                        "description": "Auto-generated function from file.",
                        "parameters": schema_data
                    }
                _function_schema_cache[schema] = fn_def
        else:
            schema_hash = hash_schema(schema)
            if schema_hash in _function_schema_cache:
                fn_def = _function_schema_cache[schema_hash]
            else:
                fn_def = {
                    "name": f"auto_function_{schema_hash[:8]}",
                    "description": "Auto-generated function from schema.",
                    "parameters": schema
                }
                _function_schema_cache[schema_hash] = fn_def

        if verbose:
            info(f"[OpenAI.openai_prompt] > ChatCompletion (function call with schema, {schema}):\n"
                  f"{textwrap.indent(textwrap.fill(user_prompt, width=80), '    ')}\n")

        messages = [{"role": "system", "content": system_message}] if system_message else []
        messages.append({"role": "user", "content": user_prompt})

        response = openai_client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=[{"type": "function", "function": fn_def}],
            tool_choice={"type": "function", "function": {"name": fn_def["name"]}}
        )

        tool_args = response.choices[0].message.tool_calls[0].function.arguments
        if verbose:
                info(f"[OpenAI.openai_prompt] > Output:\n"
                      f"{textwrap.indent(textwrap.fill(tool_args, width=80), '    ')}")
        return json.loads(tool_args)

    # === Assistant flow if files are uploaded ===
    if verbose:
        info(f"> Asking OpenAI Assistant (with files):\n"
              f"{textwrap.indent(textwrap.fill(user_prompt, width=80), '    ')}\n"
              f"{uploaded_files}")

    assistant = openai_client.beta.assistants.create(
        name="FileProcessingAssistant",
        instructions=system_message,
        model=model_name,
        tools=[{"type": "file_search"}]
    )

    thread = openai_client.beta.threads.create()
    openai_client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=user_prompt,
        attachments=[{"file_id": fid, "tools": [{"type": "file_search"}]} for fid in uploaded_files]
    )

    run = openai_client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id
    )

    while True:
        run_status = openai_client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if run_status.status == "completed":
            break
        elif run_status.status == "failed":
            raise RuntimeError(f"Assistant run failed: {run_status}")
        time.sleep(0.5)

    messages = openai_client.beta.threads.messages.list(thread_id=thread.id)
    for message in messages.data:
        if message.role == "assistant":
            result = message.content
