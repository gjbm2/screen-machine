"""utils.llm: JSON-returning LLM calls with one forced tool, model fallback (and discovery), breakers, a cost ledger,
estimates and daily budgets.

See core.py's docstring and show_service/README.md ("Stage 3 > S3.12", S5.20). Model ids, prices and budgets live in
config/llm.yaml only; keys come from the environment or the repo's .env and are never logged.
"""

from .core import (LLMResult, acall_json, call_json, check, configure, estimate, newest, openapi_schema, over_budget,
                   probe, redact, reload, status, task_settings, validate)

__all__ = ["LLMResult", "acall_json", "call_json", "check", "configure", "estimate", "newest", "openapi_schema",
           "over_budget", "probe", "redact", "reload", "status", "task_settings", "validate"]
