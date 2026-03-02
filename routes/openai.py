import os
import time
import json
import textwrap
import hashlib
import re
import jsonschema
import base64
from io import BytesIO
from PIL import Image
from openai import OpenAI
from routes.utils import findfile, resize_image_keep_aspect
from utils.logger import log_to_console, info, error, warning, debug, console_logs

_system_prompt_cache = {}
_system_prompt_mtime = {}
_function_schema_cache = {}
_function_schema_mtime = {}

def hash_schema(schema):
    return hashlib.md5(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _image_to_base64_data_url(image_input, index=0, max_dim=512, quality=90):
    """Convert an image input (file path, dict, or raw base64 string) to a
    base64 data URL suitable for the Chat Completions vision API.
    Returns the data URL string, or None on failure."""
    try:
        if isinstance(image_input, dict):
            image_bytes = base64.b64decode(image_input["image"])
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
        elif isinstance(image_input, str) and os.path.isfile(image_input):
            img = Image.open(image_input).convert("RGB")
        elif isinstance(image_input, str) and len(image_input) > 1000:
            image_bytes = base64.b64decode(image_input)
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
        else:
            warning(f"[openai_prompt] Ignored unrecognized image input: {type(image_input)}")
            return None

        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        debug(f"[openai_prompt] Image {index}: {len(buf.getvalue()):,} bytes JPEG, {len(b64):,} bytes base64")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        warning(f"[openai_prompt] Failed to process image {index}: {e}")
        return None

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

    # === Vision mode: Chat Completions with inline base64 images ===
    if images:
        if verbose:
            info(f"[OpenAI.openai_prompt] > Vision mode with {len(images)} image(s)")

        image_content_parts = []
        for i, image_input in enumerate(images):
            data_url = _image_to_base64_data_url(image_input, index=i)
            if data_url:
                image_content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": "low"}
                })

        if not image_content_parts:
            warning("[openai_prompt] No images could be processed, falling through to text-only")
        else:
            schema_obj = None
            response_format_arg = None

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
                            _function_schema_cache[schema] = {"parameters": schema_obj}
                            _function_schema_mtime[schema] = mtime
                    else:
                        schema_obj = _function_schema_cache[schema]["parameters"]
                else:
                    schema_obj = schema.get("parameters", schema)

                schema_str = json.dumps(schema_obj, indent=2)
                system_message += f"\n\n---\n\nSchema:\n{schema_str}"
                system_message += "\n\nYou must respond with only a valid JSON object matching the above schema. Do not include any explanation or prose — only the JSON object as the entire output."

                strict_schema = dict(schema_obj)
                props = strict_schema.get("properties", {})
                if "explanation" in props and props["explanation"].get("type") == "string":
                    props["explanation"] = {"type": ["string", "null"]}
                strict_schema["required"] = list(props.keys())
                strict_schema["additionalProperties"] = False

                response_format_arg = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "reasoner_output",
                        "strict": True,
                        "schema": strict_schema
                    }
                }

            user_content = [
                {"type": "text", "text": user_prompt or ""},
                *image_content_parts
            ]

            debug(f"[openai_prompt] Prompt:\n{textwrap.indent(user_prompt or '', '  ')}")
            debug(f"[openai_prompt] Sending {len(image_content_parts)} image(s) via Chat Completions vision")

            api_kwargs = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_content}
                ]
            }
            if response_format_arg:
                api_kwargs["response_format"] = response_format_arg

            response = openai_client.chat.completions.create(**api_kwargs)
            raw = response.choices[0].message.content
            debug(f"[openai_prompt] Vision response ({response.usage.prompt_tokens}+{response.usage.completion_tokens} tokens):\n{raw}")

            if schema_obj and response_format_arg:
                parsed = json.loads(raw)
                return parsed
            elif schema_obj:
                cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
                parsed = json.loads(cleaned)
                jsonschema.validate(parsed, schema_obj)
                return parsed
            else:
                try:
                    return json.loads(raw)
                except Exception:
                    return raw

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
