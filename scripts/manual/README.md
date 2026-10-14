# Manual scripts

One-off scripts moved out of the repo root on 2026-10-14. They are not tests: none of them is named `test_*.py`, so pytest never collects them.

**Several of them touch prod.** `manual_overlay_instruction.py` and `manual_ws_overlay_client.py` send real overlays (the second goes through the relay on :8765), and `manual_reference_image_persistence.py` writes a `_test_reference` bucket under the live `output/`. Run them only on purpose, from the repo root with the Flask venv and the repo on the path (they import `routes`):

```bash
PYTHONPATH=. .venv/bin/python scripts/manual/manual_scheduler_handlers.py
```

| Script | Was | What it does |
|---|---|---|
| `manual_scheduler_handlers.py` | `test.py` | Calls `handle_set_var`, `handle_random_choice` and `handle_reason` with `openai_prompt` mocked |
| `manual_handlers.py` | `test_handlers.py` | Calls `handle_set_var` and `get_display_service` |
| `manual_overlay_instruction.py` | `test_overlay_instruction.py` | Sends two real overlays to `test-destination` through `handle_overlay` |
| `manual_reference_image_persistence.py` | `test_reference_image_persistence.py` | Exercises `ReferenceImageStorage` against a temporary bucket in the live `output/` |
| `manual_ws_overlay_client.py` | `test_ws.py` | Sends an overlay to `devtest` through the prod WS relay at `ws://localhost:8765` |
