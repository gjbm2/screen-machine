"""openai_prompt: the app's prompt work (the refiner, translate, the scheduler's reasoners, Alexa's intents).

Owner, 17 Oct 2026: not tied to OpenAI. The models come from config/llm.yaml's ``prompt`` task (``alexa`` for Alexa's
intents: the same models on a short timeout), Gemini first with OpenAI as the fallback, through utils.llm.chat (keys,
discovery, breakers, the ledger). The request shapes are the ones this module always sent (Chat Completions: inline
base64 images with a JSON schema answer, one forced function tool, or plain text); they ran unchanged on Gemini's
OpenAI-compatible endpoint in the 17 Oct side-by-side. The module keeps its name so its callers don't change.
"""
import os
import json
import textwrap
import hashlib
import re
import jsonschema
import base64
from io import BytesIO
from PIL import Image
import utils.llm as llm
from routes.utils import findfile, resize_image_keep_aspect  # noqa: F401 (kept for importers)
from utils.logger import log_to_console, info, error, warning, debug, console_logs  # noqa: F401
from utils.alerts import alert

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


def _system_message(system_prompt):
    """A system prompt given as a file (cached on mtime) or as the text itself."""
    if not system_prompt:
        return ""
    path = findfile(system_prompt)
    if path and os.path.isfile(path):
        mtime = os.path.getmtime(path)
        if system_prompt not in _system_prompt_cache or _system_prompt_mtime.get(system_prompt) != mtime:
            with open(path, "r", encoding="utf-8") as f:
                _system_prompt_cache[system_prompt] = f.read()
                _system_prompt_mtime[system_prompt] = mtime
        return _system_prompt_cache[system_prompt]
    return system_prompt


def _vision_schema(schema):
    """The JSON schema object of a vision call's answer (a file, cached on mtime, or a dict)."""
    if isinstance(schema, str):
        resolved = findfile(schema)
        mtime = os.path.getmtime(resolved)
        if schema not in _function_schema_cache or _function_schema_mtime.get(schema) != mtime:
            with open(resolved, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            _function_schema_cache[schema] = {"parameters": loaded.get("parameters", loaded)}
            _function_schema_mtime[schema] = mtime
        return _function_schema_cache[schema]["parameters"]
    return schema.get("parameters", schema)


def _fn_def(schema):
    """The forced function of a text call with a schema (a file, cached on mtime, or a dict)."""
    def build(loaded, from_file):
        if "parameters" in loaded and loaded["parameters"].get("type") == "object":
            return {"name": loaded.get("name", f"auto_function_{hash_schema(loaded['parameters'])[:8]}"),
                    "description": loaded.get("description", "Auto-generated function from file." if from_file
                                              else "Auto-generated function from schema."),
                    "parameters": loaded["parameters"]}
        return {"name": f"auto_function_{hash_schema(loaded)[:8]}",
                "description": "Auto-generated function from schema.", "parameters": loaded}

    if isinstance(schema, str):
        resolved = findfile(schema)
        mtime = os.path.getmtime(resolved)
        if schema not in _function_schema_cache or _function_schema_mtime.get(schema) != mtime:
            with open(resolved, "r", encoding="utf-8") as f:
                _function_schema_cache[schema] = build(json.load(f), True)
            _function_schema_mtime[schema] = mtime
        return _function_schema_cache[schema]
    return build(schema, False)


def _unfence(text):
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _chat(task, check, request):
    """utils.llm.chat over the task's models. An operator alert when every model failed (the exception still
    reaches the caller, whose own fallbacks are unchanged), and a warning when the fallback had to answer."""
    try:
        value, label = llm.chat(task, check=check, **request)
    except llm.LLMError as e:
        if e.blocked:                                 # a safety refusal: final (no other provider), logged only
            alert("llm.blocked", f"LLM prompt refused on safety grounds ({task})", severity="info",
                  context={"task": task, "model": (e.attempts or [{}])[-1].get("model")})
            raise
        tried = "; ".join(f"{a['model']}: {a['error']}" for a in e.attempts)[:500]
        quota = any(s in tried.lower() for s in ("quota", "credit", "402", "billing"))
        alert("openai.quota" if quota else "openai.error",
              f"LLM prompt failed on every model ({task})" + (" - out of credit?" if quota else ""),
              severity="critical" if quota else "error", exc=e, context={"task": task, "tried": tried})
        raise
    first = ((llm.task_settings(task).get("models") or [{}])[0]).get("provider")
    if first and not label.startswith(f"{first}/"):
        alert("llm.fallback", f"LLM prompt answered by the fallback {label} ({task})", severity="warning",
              context={"task": task, "model": label})
    debug(f"[openai_prompt] answered by {label}")
    return value


def openai_prompt(
    user_prompt,
    system_prompt=None,
    model_name=None,
    upload=None,
    schema=None,
    images=None,
    verbose=True,
    task="prompt",
):
    """One prompt on config/llm.yaml's ``task`` ("prompt"; "alexa" for Alexa's intents, on a short timeout).
    ``model_name`` is ignored (kept for old callers): llm.yaml picks the models."""
    if model_name:
        debug(f"[openai_prompt] model_name={model_name!r} ignored: config/llm.yaml's {task!r} task picks the models")
    system_message = _system_message(system_prompt)

    # === Vision mode: inline base64 images, a JSON schema answer ===
    if images:
        if verbose:
            info(f"[openai_prompt] > Vision mode with {len(images)} image(s)")
        image_content_parts = []
        for i, image_input in enumerate(images):
            data_url = _image_to_base64_data_url(image_input, index=i)
            if data_url:
                image_content_parts.append({"type": "image_url", "image_url": {"url": data_url, "detail": "low"}})

        if not image_content_parts:
            warning("[openai_prompt] No images could be processed, falling through to text-only")
        else:
            schema_obj = _vision_schema(schema) if schema else None
            request = {}
            if schema_obj:
                system_message += f"\n\n---\n\nSchema:\n{json.dumps(schema_obj, indent=2)}"
                system_message += ("\n\nYou must respond with only a valid JSON object matching the above schema. "
                                   "Do not include any explanation or prose — only the JSON object as the entire output.")
                strict_schema = dict(schema_obj)
                props = strict_schema.get("properties", {})
                if "explanation" in props and props["explanation"].get("type") == "string":
                    props["explanation"] = {"type": ["string", "null"]}
                strict_schema["required"] = list(props.keys())
                strict_schema["additionalProperties"] = False
                request["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "reasoner_output", "strict": True, "schema": strict_schema}}
            debug(f"[openai_prompt] Prompt:\n{textwrap.indent(user_prompt or '', '  ')}")
            debug(f"[openai_prompt] Sending {len(image_content_parts)} image(s) with the prompt")
            request["messages"] = [{"role": "system", "content": system_message},
                                   {"role": "user", "content": [{"type": "text", "text": user_prompt or ""},
                                                                *image_content_parts]}]

            def check_vision(resp):
                raw = resp.choices[0].message.content or ""
                debug(f"[openai_prompt] Vision response:\n{raw}")
                if schema_obj:
                    parsed = json.loads(_unfence(raw))
                    jsonschema.validate(parsed, schema_obj)
                    return parsed
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
            return _chat(task, check_vision, request)

    messages = ([{"role": "system", "content": system_message}] if system_message else []) + \
        [{"role": "user", "content": user_prompt}]

    # === Simple flow: plain text (e.g. translate) ===
    if not upload and not schema:
        def check_text(resp):
            result = (resp.choices[0].message.content or "").strip()
            if not result:
                raise ValueError("an empty answer")
            try:
                return json.loads(_unfence(result))
            except Exception:
                return result
        return _chat(task, check_text, {"messages": messages})

    # === Function mode (no image): one forced tool ===
    if schema:
        fn_def = _fn_def(schema)

        def check_tool(resp):
            return json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        return _chat(task, check_tool, {"messages": messages, "tools": [{"type": "function", "function": fn_def}],
                                        "tool_choice": {"type": "function", "function": {"name": fn_def["name"]}}})
