import os
import time
import json
import textwrap
import hashlib
import re
import jsonschema
import base64
from uuid import uuid4
from io import BytesIO
from PIL import Image
from werkzeug.utils import secure_filename
from openai import OpenAI
from routes.utils import findfile, resize_image_keep_aspect
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# Caches
_system_prompt_cache = {}
_system_prompt_mtime = {}
_file_upload_cache = {}
_function_schema_cache = {}
_function_schema_mtime = {}

def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def hash_schema(schema):
    return hashlib.md5(json.dumps(schema, sort_keys=True).encode()).hexdigest()

def openai_prompt(
    user_prompt,
    system_prompt=None,
    model_name="gpt-4o",
    upload=None,
    schema=None,
    images=None,
    verbose=True
):
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment. Check your .env file or environment variables.")
    openai_client = OpenAI(api_key=openai_key)

    # Load system prompt
    if system_prompt:
        path = findfile(system_prompt)
        if path and os.path.isfile(path):
            mtime = os.path.getmtime(path)
            if (
                system_prompt not in _system_prompt_cache or
                _system_prompt_mtime.get(system_prompt) != mtime
            ):
                with open(path, "r", encoding="utf-8") as f:
                    _system_prompt_cache[system_prompt] = f.read()
                    _system_prompt_mtime[system_prompt] = mtime
            system_message = _system_prompt_cache[system_prompt]
        else:
            system_message = system_prompt
    else:
        system_message = ""

    # === Assistant mode if images are passed ===
    if images:
        if verbose:
            info(f"[OpenAI.openai_prompt] > Assistant mode with image upload")

        resized_paths = []
        for i, image_input in enumerate(images):
            try:
                if isinstance(image_input, dict):
                    image_bytes = base64.b64decode(image_input["image"])
                    image = Image.open(BytesIO(image_bytes)).convert("RGB")
                    image.thumbnail((512, 512), Image.LANCZOS)
                    tmp_path = f"/tmp/{secure_filename(image_input.get('name', f'image_{i}.jpg'))}"
                    image.save(tmp_path, format="JPEG", quality=90)
                    resized_paths.append(tmp_path)

                elif isinstance(image_input, str) and os.path.isfile(image_input):
                    img = resize_image_keep_aspect(image_input, max_dim=512)
                    resized_path = f"{image_input}.resized.jpg"
                    
                    # Force remove old cached file if it exists
                    if os.path.exists(resized_path):
                        try:
                            os.remove(resized_path)
                            debug(f"[openai_prompt] Removed old cached resized file: {resized_path}")
                        except Exception as e:
                            warning(f"[openai_prompt] Failed to remove old cached file {resized_path}: {e}")
                    
                    img.save(resized_path, format="JPEG", quality=90)
                    debug(f"[openai_prompt] Created new resized file: {resized_path}")
                    resized_paths.append(resized_path)

                elif isinstance(image_input, str) and len(image_input) > 1000:
                    image_bytes = base64.b64decode(image_input)
                    image = Image.open(BytesIO(image_bytes)).convert("RGB")
                    image.thumbnail((512, 512), Image.LANCZOS)
                    tmp_path = f"/tmp/openai_upload_{uuid4().hex}.jpg"
                    image.save(tmp_path, format="JPEG", quality=90)
                    resized_paths.append(tmp_path)

                else:
                    warning(f"[openai_prompt] Ignored unrecognized image input: {type(image_input)}")

            except Exception as e:
                warning(f"[openai_prompt] Failed to process image {i}: {e}")

        uploaded_files = []
        for path in resized_paths:
            file_hash = hash_file(path)
            if file_hash in _file_upload_cache:
                file_id = _file_upload_cache[file_hash]
            else:
                with open(path, "rb") as f:
                    uploaded = openai_client.files.create(file=f, purpose="vision")
                    file_id = uploaded.id
                    _file_upload_cache[file_hash] = file_id
            uploaded_files.append(file_id)

        if schema:
            if isinstance(schema, str):
                resolved = findfile(schema)
                mtime = os.path.getmtime(resolved)
                if (
                    schema not in _function_schema_cache or
                    _function_schema_mtime.get(schema) != mtime
                ):
                    with open(resolved, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        schema_obj = loaded.get("parameters", loaded)
                        schema_str = json.dumps(schema_obj, indent=2)
                        _function_schema_cache[schema] = {"parameters": schema_obj}
                        _function_schema_mtime[schema] = mtime
                else:
                    schema_obj = _function_schema_cache[schema]["parameters"]
                    schema_str = json.dumps(schema_obj, indent=2)
            else:
                schema_obj = schema.get("parameters", schema)
                schema_str = json.dumps(schema_obj, indent=2)

            system_message += f"\n\n---\n\nSchema:\n{schema_str}"
            system_message += "\n\nYou must respond with only a valid JSON object matching the above schema. Do not include any explanation or prose — only the JSON object as the entire output."

        # Retry loop with validation
        for attempt in range(3):
            assistant = openai_client.beta.assistants.create(
                name="ImageAssistant",
                instructions=system_message,
                model=model_name,
                tools=[]
            )

            thread = openai_client.beta.threads.create()

            try:
                openai_client.beta.threads.messages.create(
                    thread_id=thread.id,
                    role="user",
                    content=[
                        {"type": "text", "text": user_prompt or ""},
                        *[
                            {
                                "type": "image_file",
                                "image_file": {
                                    "file_id": fid,
                                    "detail": "auto"
                                }
                            } for fid in uploaded_files
                        ]
                    ]
                )
            except Exception as e:
                error(f"[openai_prompt] Failed to create thread message: {e}")
                raise

            debug(f"[openai_prompt] Prompt:\n{textwrap.indent(user_prompt or '', '  ')}")
            debug(f"[openai_prompt] Uploaded image file IDs: {uploaded_files}")

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
                    try:
                        raw = message.content[0].text.value.strip()
                        debug(f"[openai_prompt] Assistant raw output:\n{raw}")

                        if not raw:
                            raise ValueError("Assistant returned an empty response")

                        # Remove potential ``` wrappers from assistant output
                        cleaned_raw = raw.strip()
                        if cleaned_raw.startswith("```"):
                            cleaned_raw = cleaned_raw.lstrip("` ")
                            if cleaned_raw.lower().startswith("json"):
                                cleaned_raw = cleaned_raw[4:].lstrip()
                            cleaned_raw = cleaned_raw.rstrip("`").rstrip()
                        parsed = json.loads(cleaned_raw)
                        debug(f"[openai_prompt] Parsed JSON:\n{json.dumps(parsed, indent=2)}")

                        if schema:
                            jsonschema.validate(parsed, schema_obj)
                            debug("[openai_prompt] ✅ Schema validation passed")

                        return parsed

                    except json.JSONDecodeError as e:
                        warning(f"[Assistant retry] Attempt {attempt+1}: Failed to parse JSON — {e}")
                        warning(f"[Assistant retry] Raw output: {repr(raw)}")

                    except jsonschema.ValidationError as e:
                        warning(f"[Assistant retry] Attempt {attempt+1}: Schema validation failed — {e.message}")
                        warning(f"[Assistant retry] Invalid JSON: {json.dumps(parsed, indent=2)}")

                    except Exception as e:
                        warning(f"[Assistant retry] Attempt {attempt+1}: Unknown error — {e}")

        raise RuntimeError("Assistant failed to return valid schema-compliant output after 3 attempts.")

    # === Simple flow ===
    if not upload and not schema:
        messages = [{"role": "system", "content": system_message}] if system_message else []
        messages.append({"role": "user", "content": user_prompt})

        response = openai_client.chat.completions.create(
            model=model_name,
            messages=messages
        )

        result = response.choices[0].message.content.strip()
        cleaned = re.sub(r"^```(?:json)?|```$", "", result.strip(), flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except Exception:
            return result

    # === Function mode (no image) ===
    if schema:
        if isinstance(schema, str):
            resolved = findfile(schema)
            mtime = os.path.getmtime(resolved)
            if (
                schema not in _function_schema_cache or
                _function_schema_mtime.get(schema) != mtime
            ):
                with open(resolved, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if "parameters" in loaded and loaded["parameters"].get("type") == "object":
                    fn_def = {
                        "name": loaded.get("name", f"auto_function_{hash_schema(loaded['parameters'])[:8]}"),
                        "description": loaded.get("description", "Auto-generated function from file."),
                        "parameters": loaded["parameters"]
                    }
                else:
                    fn_def = {
                        "name": f"auto_function_{hash_schema(loaded)[:8]}",
                        "description": "Auto-generated function from schema.",
                        "parameters": loaded
                    }
                _function_schema_cache[schema] = fn_def
                _function_schema_mtime[schema] = mtime
            else:
                fn_def = _function_schema_cache[schema]
        else:
            if "parameters" in schema and schema["parameters"].get("type") == "object":
                fn_def = {
                    "name": schema.get("name", f"auto_function_{hash_schema(schema['parameters'])[:8]}"),
                    "description": schema.get("description", "Auto-generated function from schema."),
                    "parameters": schema["parameters"]
                }
            else:
                fn_def = {
                    "name": f"auto_function_{hash_schema(schema)[:8]}",
                    "description": "Auto-generated function from schema.",
                    "parameters": schema
                }

        messages = [{"role": "system", "content": system_message}] if system_message else []
        messages.append({"role": "user", "content": user_prompt})

        response = openai_client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=[{"type": "function", "function": fn_def}],
            tool_choice={"type": "function", "function": {"name": fn_def["name"]}}
        )

        tool_args = response.choices[0].message.tool_calls[0].function.arguments
        return json.loads(tool_args)
