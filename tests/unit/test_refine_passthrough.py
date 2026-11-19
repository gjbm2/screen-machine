"""routes.generate_handler._refine: when every prompt model fails, the user's own words go through (passthrough)."""
import routes.openai
import routes.utils
from routes import generate_handler
from utils.llm import LLMError


def test_refiner_failure_passes_the_prompt_through(monkeypatch):
    def fail(**kw):
        raise LLMError("prompt", "every model failed", [])
    monkeypatch.setattr(routes.openai, "openai_prompt", fail)
    monkeypatch.setattr(routes.utils, "dict_substitute", lambda name, subs: "{}" if name.endswith(".json.j2") else "x")
    monkeypatch.setattr(routes.utils, "resolve_runtime_value", lambda **kw: "qwen21-turbo.json")
    wf, refined, final, _ = generate_handler._refine({}, "a nude by Modigliani", "qwen21-turbo.json", [], "enrich", False, [],
                                                  {"prompt": "a nude by Modigliani"}, {})
    assert refined == "a nude by Modigliani" and final == refined


def test_a_safety_block_is_not_passed_through(monkeypatch):
    def blocked(**kw):
        raise LLMError("prompt", "PROHIBITED_CONTENT", [], blocked=True)
    monkeypatch.setattr(routes.openai, "openai_prompt", blocked)
    monkeypatch.setattr(routes.utils, "dict_substitute", lambda name, subs: "{}" if name.endswith(".json.j2") else "x")
    import pytest
    with pytest.raises(LLMError):
        generate_handler._refine({}, "x", "qwen21-turbo.json", [], "enrich", False, [], {"prompt": "x"}, {})
