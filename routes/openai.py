import os
import time
import textwrap
import hashlib
from openai import OpenAI
from routes.utils import findfile

# Caches to store system prompts and uploaded file IDs
_system_prompt_cache = {}
_file_upload_cache = {}  # maps hash(filepath) -> file_id


def hash_file(path):
    """Create a unique hash of the file contents to avoid redundant uploads."""
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def openai_prompt(user_prompt, system_prompt=None, model_name="gpt-4o", upload=None):
    """
    Sends a user prompt to OpenAI with an optional system prompt and file uploads.

    Parameters:
    - user_prompt (str): The main prompt from the user.
    - system_prompt (str or None): System instructions as text or a file path.
    - model_name (str): The OpenAI model to use (default is "gpt-4o").
    - upload (str or list of str or None): File path(s) to upload.

    Returns:
    - str: The assistant's response.
    """
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # === System prompt loading and caching ===
    if system_prompt:
        if system_prompt in _system_prompt_cache:
            system_message = _system_prompt_cache[system_prompt]
        else:
            system_prompt_path = findfile(system_prompt)
            if system_prompt_path and os.path.isfile(system_prompt_path):
                with open(system_prompt_path, "r", encoding="utf-8") as f:
                    system_message = f.read()
                _system_prompt_cache[system_prompt] = system_message
            else:
                system_message = system_prompt  # Assume it's inline text
    else:
        system_message = ""

    # === File upload caching and handling ===
    uploaded_files = []
    if upload:
        if isinstance(upload, str):
            upload = [upload]
        for filepath in upload:
            resolved_path = findfile(filepath)
            if resolved_path and os.path.isfile(resolved_path):
                abs_path = os.path.abspath(resolved_path)
                file_hash = hash_file(abs_path)
                if file_hash in _file_upload_cache:
                    file_id = _file_upload_cache[file_hash]
                else:
                    with open(abs_path, "rb") as f:
                        uploaded_file = openai_client.files.create(file=f, purpose="assistants")
                        file_id = uploaded_file.id
                        _file_upload_cache[file_hash] = file_id
                uploaded_files.append(file_id)
                print(f"[SUCCESS] Cached or uploaded: {filepath} => {file_id}")
            else:
                print(f"[WARNING] File not found or invalid: {filepath}")

    # === Display request ===
    print(f"> Asking OpenAI (with system prompt '{system_prompt}'):\n"
          f"{textwrap.indent(textwrap.fill(user_prompt, width=80), '    ')}\n"
          f"{uploaded_files}")

    # === Create assistant ===
    assistant = openai_client.beta.assistants.create(
        name="FileProcessingAssistant",
        instructions=system_message,
        model=model_name,
        tools=[{"type": "file_search"}] if uploaded_files else []
    )

    # === Create a thread and add the user message ===
    thread = openai_client.beta.threads.create()
    openai_client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=user_prompt,
        attachments=[
            {"file_id": fid, "tools": [{"type": "file_search"}]}
            for fid in uploaded_files
        ] if uploaded_files else None
    )

    # === Run the assistant ===
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

    # === Retrieve response ===
    messages = openai_client.beta.threads.messages.list(thread_id=thread.id)
    for message in messages.data:
        if message.role == "assistant":
            return message.content[0].text.value.strip()

    return "No assistant reply found."