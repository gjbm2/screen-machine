# Proposal: Fix OpenAI Image Reasoning & Scheduler Performance

**Date:** 2026-03-02
**Status:** Proposed

---

## 1. Problem Summary

The-beast scheduler has been stuck in an infinite generate-retry loop since
~Feb 11 2026. It has produced **0 successful publishes** while burning through
**9,992 image generations** (~40,000 images, **11 GB** of disk), **20,017 failed
OpenAI API calls**, and accumulating **273,597 in-memory log entries**.

Other schedulers (cloud, south-screen, north-screen) are functional but carry
a separate performance issue: bloated state files from unbounded
`last_trigger_executions` growth (already patched earlier in this session).

---

## 2. Root Causes

### 2a. OpenAI Assistants API `image_file` is broken (external)

Since ~Feb 11 2026, OpenAI's Assistants API returns `server_error` for every
run that includes `image_file` attachments. Text-only runs work fine. This is
a **known, acknowledged OpenAI bug** reported by multiple developers:

- https://community.openai.com/t/error-when-attaching-images-via-assistants-api/1374062
- https://community.openai.com/t/uploading-images-to-assistants-what-happens-next/1374269

OpenAI has not fixed it. The Assistants API is deprecated (shutdown Aug 26
2026) and they are directing developers to migrate to the Responses API or
Chat Completions API.

**Current code path** (`routes/openai.py` lines 63-230):

1. Resize images to 512×512, save as JPEG to disk
2. Upload each file via `client.files.create(purpose="vision")`
3. Create a **new** Assistant, Thread, and Run for every single call
4. Attach images as `image_file` content parts in the thread message
5. Poll run status until complete or failed
6. Parse JSON from the assistant's text response, validate against schema

Every step after (2) now fails with `server_error`.

### 2b. Error fallback creates infinite retry loop

When `handle_reason` (`routes/scheduler_handlers.py` line 1528) catches the
exception, it sets:

```python
context["vars"][output_vars[0]] = image_inputs[0]   # a FILE PATH
```

This means:
- `selected_index` gets set to a file path (should be `"0"`, `"1"`, etc.)
- `is_safe` gets set to a file path (should be `"APPROVE"` or `"REJECT"`)

Downstream, the schedule checks `{{ is_safe == 'APPROVE' }}` → `False`, so:
- The `publish` instruction gets an empty source → fails silently
- The `terminate` test returns `False` → does not break the loop
- A "Retry - previous image was not safe" event fires with 10s delay
- The cycle repeats indefinitely

### 2c. Scheduler logs have no size cap

`scheduler_logs` is a plain `Dict[str, List[str]]` with no maximum length.
The-beast has accumulated 273,597 entries (~30+ MB of strings in memory).
This affects API response times for any endpoint that reads or serializes
scheduler state.

### 2d. Leftover `.resized.jpg` files on disk

The current code saves resized images as `{original_path}.resized.jpg` and
never cleans them up. There are **6,501** orphaned `.resized.jpg` files across
the output directory.

---

## 3. Proposed Changes

### Change 1: Replace Assistants API with Chat Completions + Vision

**File:** `routes/openai.py`

Replace the entire `if images:` block (lines 63-230) with a Chat Completions
API call using inline base64 images.

**New approach:**

1. **Keep** the existing image preprocessing (resize to 512×512, JPEG
   quality 90). This keeps token costs low and payload sizes small.
2. **Encode** each resized image as a base64 data URL in memory (no file
   upload to OpenAI, no leftover disk files).
3. **Send** via `client.chat.completions.create()` with:
   - `model=model_name` (currently `"gpt-4o"`)
   - System message as `{"role": "system", "content": system_message}`
   - User message with `{"type": "text"}` + `{"type": "image_url"}` parts
   - `response_format={"type": "json_schema", ...}` for structured output
4. **Remove** the file upload cache (`_file_upload_cache`), assistant
   creation, thread creation, run polling, and manual JSON parsing/retrying.

**Image compression detail:**

Images are already resized to 512×512 via `resize_image_keep_aspect()` and
saved as JPEG quality 90. The resized files are typically 25-50 KB. Rather
than writing to disk and then reading back for base64 encoding, the new code
will encode from the PIL Image object directly into a BytesIO buffer,
avoiding any temp file. The `detail` parameter will be `"low"` (85 tokens
per image) since 512×512 is already our target resolution.

**Schema handling:**

The current schema (`routes/data/reasoner.schema.json.j2`) resolves to:

```json
{
  "type": "object",
  "properties": {
    "outputs": {
      "type": "array",
      "items": { "type": "string" }
    },
    "explanation": {
      "type": "string"
    }
  },
  "required": ["outputs"]
}
```

For Structured Outputs (`response_format` with `json_schema` and
`strict: true`), OpenAI requires:
- All properties listed in `"required"`
- `"additionalProperties": false`

So the schema passed to `response_format` will be:

```json
{
  "type": "object",
  "properties": {
    "outputs": {
      "type": "array",
      "items": { "type": "string" }
    },
    "explanation": {
      "type": ["string", "null"]
    }
  },
  "required": ["outputs", "explanation"],
  "additionalProperties": false
}
```

This guarantees the model always returns schema-compliant JSON. The schema
text will still be appended to the system prompt (as currently done) for
the model's benefit, but the `response_format` parameter enforces it
server-side.

**Backward compatibility:**

- The function signature of `openai_prompt()` does not change.
- The three existing code paths remain:
  1. `images` provided → **NEW**: Chat Completions with vision
  2. No images, no schema → Simple Chat Completions (unchanged)
  3. No images, schema provided → Function-calling Chat Completions (unchanged)
- All callers (`scheduler_handlers.py`, `generate_handler.py`) are
  unaffected.

### Change 2: Cap scheduler logs

**Files:** `config.py`, `routes/scheduler_utils.py`

Add `MAX_SCHEDULER_LOG_SIZE = 5000` to `config.py` and enforce it in
`log_schedule()`:

```python
if len(scheduler_logs[dest]) > MAX_SCHEDULER_LOG_SIZE:
    scheduler_logs[dest] = scheduler_logs[dest][-MAX_SCHEDULER_LOG_SIZE:]
```

### Change 3: Clean up resized temp files

**File:** `routes/openai.py`

With the new approach, resized images are encoded to base64 from an in-memory
PIL buffer — no `.resized.jpg` file is written to disk at all. This
eliminates the source of orphan files going forward.

Existing orphan `.resized.jpg` files can be cleaned up manually or via a
one-time script.

### Change 4 (already applied): Prune `last_trigger_executions`

Applied earlier in this session. Entries older than 2 days are pruned on
save/load, reducing state file sizes from ~1 MB to ~10-50 KB per scheduler.

---

## 4. What This Does NOT Change

- **Schedule definitions** (`the-beast.json`, etc.) — no changes needed.
- **The `handle_reason` fallback** — the fallback sets `output_vars[0]` to
  the first image path. With the API fix, this fallback should rarely be
  reached. If it is, the same retry loop would occur, but that's a
  pre-existing design choice in the schedule (not a code bug). A future
  improvement could add a retry counter, but that's out of scope here.
- **The `generate_handler.py` callers** — these use `openai_prompt()`
  without images (for prompt refinement/translation), so they are
  unaffected.
- **The non-image code paths** in `openai_prompt()` (simple flow and
  function mode) — these remain untouched.

---

## 5. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Chat Completions vision doesn't support the current schema format | Low | The schema is simple (string array + optional string). Structured Outputs supports this. Validated by test plan below. |
| Base64 encoding increases request payload size | N/A | Images are resized to 512×512 JPEG q90 (~25-50 KB). Base64 adds ~33% → ~33-67 KB per image. 4 images ≈ 130-270 KB total payload. Well within API limits (50 MB). |
| Token costs change | Low | At `detail: "low"`, each image costs 85 tokens. 4 images = 340 tokens. Similar to the Assistants approach. |
| `strict: true` schema requires all fields in `required` | Medium | `explanation` will be typed as `["string", "null"]` so the model can omit it by returning `null`. Validated by test. |
| Structured Outputs doesn't work with vision inputs | Low | OpenAI docs confirm Chat Completions supports both vision and structured outputs. Validated by test. |
| Chat Completions API outage | Low | Chat Completions is OpenAI's primary API surface. Far more reliable than the deprecated Assistants API. |

---

## 6. Test Plan

All tests run **before any production code changes**, using a standalone
script (`test_vision_api.py`) against the live OpenAI API with the actual
project's API key, images, system prompts, and schema.

### Test 1: Basic Vision Round-Trip (single image, no schema)

**Purpose:** Confirm Chat Completions can receive a base64-encoded image and
return a text response.

**Steps:**
1. Load a real image from `output/the-beast/` (a recent `.jpg` file).
2. Resize to 512×512 using `PIL.Image.thumbnail()`, encode as JPEG q90 into
   a `BytesIO` buffer, then base64-encode.
3. Call `client.chat.completions.create()` with:
   - `model="gpt-4o"`
   - System message: `"Describe this image in one sentence."`
   - User content: `[{"type": "text", "text": "Describe"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{b64}", "detail": "low"}}]`
4. Print the response content.

**Pass criteria:**
- HTTP 200, no errors.
- Response contains a coherent image description.
- Confirm `usage` field shows token counts (image tokens + text tokens).

### Test 2: Vision + Structured Output (single image, schema enforced)

**Purpose:** Confirm `response_format` with `json_schema` works alongside
vision inputs and returns guaranteed-valid JSON.

**Steps:**
1. Same image prep as Test 1.
2. Call with the **actual reasoner schema** adapted for Structured Outputs:
   ```python
   response_format={
       "type": "json_schema",
       "json_schema": {
           "name": "reasoner_output",
           "strict": True,
           "schema": {
               "type": "object",
               "properties": {
                   "outputs": {
                       "type": "array",
                       "items": {"type": "string"}
                   },
                   "explanation": {
                       "type": ["string", "null"]
                   }
               },
               "required": ["outputs", "explanation"],
               "additionalProperties": False
           }
       }
   }
   ```
3. System message: the **actual** `image_criteria_evaluator` prompt text
   from `routes/data/reasoners/image_criteria_evaluator.txt.j2`.
4. User prompt: `"Approve only if image is suitable for display in an
   office context (no nudity or overtly sexual content)."`
5. Parse response as JSON.

**Pass criteria:**
- Response is valid JSON matching the schema.
- `outputs[0]` is either `"APPROVE"` or `"REJECT"`.
- `outputs[1]` is a percentage string (e.g. `"92%"`).
- `outputs[2]` is a short rationale string.
- `explanation` is a string or null.

### Test 3: Batch Image Selection (4 images, schema enforced)

**Purpose:** Simulate the exact `image_batch_selector` call the-beast makes:
4 images in, expect a zero-based index or `"NONE"`.

**Steps:**
1. Load 4 different images from `output/the-beast/`.
2. Resize and base64-encode each (512×512, JPEG q90, `detail: "low"`).
3. Call with:
   - System message: the **actual** `image_batch_selector` prompt text from
     `routes/data/reasoners/image_batch_selector.txt.j2`.
   - User content: text prompt + 4 `image_url` parts.
   - `response_format`: same schema as Test 2.
4. User prompt: `"Select a visually compelling image with the text 'action
   this day' clearly visible, with correct spelling and letterforms."`

**Pass criteria:**
- Response is valid JSON matching the schema.
- `outputs[0]` is one of `"0"`, `"1"`, `"2"`, `"3"`, or `"NONE"`.
- `outputs[1]` is a percentage string.
- `outputs[2]` is a short rationale string.
- Total token usage is reasonable (< 2000 tokens).

### Test 4: Schema with `explanation` as null

**Purpose:** Confirm the model can return `null` for the optional
`explanation` field when using strict schema.

**Steps:**
1. Same as Test 2 but with system prompt explicitly saying "Do not include
   an explanation."
2. Parse response.

**Pass criteria:**
- `explanation` is `null` (not a string, not missing).
- `outputs` array is still present and valid.

### Test 5: Payload Size & Latency

**Purpose:** Confirm base64-encoded images don't cause payload/latency
issues.

**Steps:**
1. Encode 4 images at 512×512 JPEG q90.
2. Measure: total base64 payload size in bytes.
3. Measure: wall-clock time from API call to response.
4. Record `usage.prompt_tokens` and `usage.completion_tokens`.

**Pass criteria:**
- Total payload < 500 KB.
- Latency < 15 seconds (typical for gpt-4o vision).
- Prompt tokens < 2000 (340 for images at low detail + system/user text).

### Test 6: Full Round-Trip Simulation

**Purpose:** Simulate the complete the-beast generate event flow end-to-end,
outside the scheduler, to confirm the new `openai_prompt()` function will
produce the right outputs for downstream schedule instructions.

**Steps:**
1. Load 4 images (simulating a batch generation).
2. **Step A — Batch Selection:** Call with `image_batch_selector` system
   prompt, 4 images, schema. Capture `selected_index` from `outputs[0]`.
3. Verify `selected_index` is a valid integer string or `"NONE"`.
4. If not `"NONE"`, select the image at that index.
5. **Step B — Safety Evaluation:** Call with `image_criteria_evaluator`
   system prompt, 1 image (the selected one), schema. Capture `is_safe`
   from `outputs[0]`.
6. Verify `is_safe` is `"APPROVE"` or `"REJECT"`.
7. Simulate the downstream logic:
   - If `is_safe == "APPROVE"`: source = `images[int(selected_index)]`
     → verify this is a valid image path. Terminate = True (loop breaks).
   - If `is_safe == "REJECT"`: source = `""`, Terminate = False (retry).

**Pass criteria:**
- Step A returns a valid index (0-3) or `"NONE"`.
- Step B returns `"APPROVE"` or `"REJECT"`.
- If `"APPROVE"`: simulated publish source is a real image path,
  simulated terminate evaluates to `True`.
- If `"REJECT"`: simulated terminate evaluates to `False` (this is correct
  behaviour — the schedule's retry logic kicks in).
- No exceptions thrown at any step.

### Test 7: Error Handling / Fallback Behaviour

**Purpose:** Confirm behaviour when API returns an error or unexpected
response.

**Steps:**
1. Call with an invalid API key → expect `AuthenticationError`.
2. Call with an empty image list → expect it falls through to non-image path.
3. Call with a corrupted base64 string → confirm error is raised cleanly.

**Pass criteria:**
- Each case raises a clear, catchable exception.
- No hangs or infinite loops.

---

## 7. Implementation Order

1. **Write and run `test_vision_api.py`** (Tests 1-7 above). Only proceed
   if all tests pass.
2. **Modify `routes/openai.py`** — replace the Assistants API image block
   with the tested Chat Completions + Vision approach.
3. **Add scheduler log cap** to `config.py` and `routes/scheduler_utils.py`.
4. **Restart the app** and manually trigger a generate event for the-beast.
   Monitor the scheduler log to confirm the full live flow:
   `generate → reason (batch select) → reason (safety) → publish`.
5. **Clean up** orphan `.resized.jpg` files from `output/`.
