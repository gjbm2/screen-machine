"""utils/llm is shared plumbing: real tests through the real openai SDK against a fake OpenAI-compatible server on an
ephemeral 127.0.0.1 port (llm.yaml's base_url). No network, no real key, no real ledger or .env."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import threading
import time

import pytest
from aiohttp import web

import utils.llm as llm
from utils.llm import core

SECRET = "sk-test-SECRETKEY0123456789abcdef"
TOOL = {"name": "report", "description": "say it",
        "parameters": {"type": "object",
                       "properties": {"word": {"type": "string", "enum": ["a", "b"]},
                                      "n": {"type": "integer", "minimum": 0, "maximum": 3}},
                       "required": ["word", "n"]}}


class FakeOpenAI:
    """GET /v1/models, POST /v1/chat/completions; behaviour per model id."""

    def __init__(self) -> None:
        self.models = ["m1", "m2", "m3"]
        self.behaviour: dict[str, str] = {}
        self.requests: list[dict] = []
        self.model_lists = 0
        self.loop = asyncio.new_event_loop()
        self.port = 0
        self._ready = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        assert self._ready.wait(10)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_get("/v1/models", self._list)
        app.router.add_post("/v1/chat/completions", self._chat)
        self.runner = web.AppRunner(app)
        self.loop.run_until_complete(self.runner.setup())
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        site = web.SockSite(self.runner, s)
        self.loop.run_until_complete(site.start())
        self._ready.set()
        self.loop.run_forever()

    def stop(self) -> None:
        async def _down():
            await self.runner.cleanup()
        asyncio.run_coroutine_threadsafe(_down(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(5)
        self.loop.close()

    async def _list(self, request: web.Request) -> web.Response:
        self.model_lists += 1
        return web.json_response({"object": "list", "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "test"} for m in self.models]})

    async def _chat(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        model = body["model"]
        b = self.behaviour.get(model, "ok")
        if b == "slow":
            await asyncio.sleep(3.0)
        if b == "500":
            return web.json_response({"error": {"message": "boom", "type": "server_error"}}, status=500)
        if b == "401":
            return web.json_response({"error": {"message": f"Incorrect API key provided: {SECRET}. Find yours.",
                                                "type": "invalid_request_error", "code": "invalid_api_key"}},
                                     status=401)
        if b == "tool_400":
            return web.json_response({"error": {"message": "This model does not support tools.",
                                                "type": "invalid_request_error"}}, status=400)
        args = {"ok": {"word": "a", "n": 2}, "slow": {"word": "a", "n": 2}, "invalid": {"word": "zzz", "n": 9}}.get(b)
        arguments = "{not json" if b == "bad_json" else json.dumps(args)
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "report", "arguments": arguments}}]}
        if b == "no_tool":
            msg = {"role": "assistant", "content": "I would rather chat."}
        return web.json_response({
            "id": "cmpl-1", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": msg}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
                      "prompt_tokens_details": {"audio_tokens": 200, "cached_tokens": 100}}})


@pytest.fixture(scope="module")
def server():
    s = FakeOpenAI()
    yield s
    s.stop()


@pytest.fixture
def layer(server, tmp_path, monkeypatch):
    """A fresh layer on tmp files; returns a writer for llm.yaml."""
    server.behaviour.clear()
    server.requests.clear()
    server.models = ["m1", "m2", "m3"]
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    cfg = tmp_path / "llm.yaml"
    env = tmp_path / ".env"
    env.write_text("")
    ledger = tmp_path / "ledger.jsonl"

    def write(models=("m1", "m2"), budgets=None, timeout_s=2, extra=""):
        lines = ["version: 1", "providers:", "  openai:", "    key_env: OPENAI_API_KEY",
                 f"    base_url: http://127.0.0.1:{server.port}/v1", "tasks:", "  t:", "    models:"]
        lines += [f"      - {{provider: openai, model: {m}}}" for m in models]
        lines += [f"    timeout_s: {timeout_s}", "    max_output_tokens: 50", "prices:",
                  "  m1: {text_in: 2.0, text_out: 10.0, audio_in: 30.0, cached_in: 1.0}",
                  "  m2: {text_in: 1.0, text_out: 1.0}"]
        if budgets:
            lines += ["budgets:"] + [f"  {k}: {v}" for k, v in budgets.items()]
        cfg.write_text("\n".join(lines) + "\n" + extra)
        llm.reload(force=True)

    llm.configure(config_path=cfg, env_file=env, ledger_path=ledger)
    write()
    write.cfg, write.env, write.ledger = cfg, env, ledger
    yield write
    llm.configure()                                   # back to the real paths (nothing read until used)


def _ledger(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# -- the validator ------------------------------------------------------------------------------------------------------
def test_validate_covers_the_keywords():
    s = {"type": "object", "required": ["a", "b"], "additionalProperties": False,
         "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 3},
                        "b": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["x", "y"]}},
                        "c": {"type": ["number", "null"]}, "d": {"type": "string", "maxLength": 3}}}
    assert llm.validate({"a": 2, "b": ["x"], "c": None}, s) == []
    assert llm.validate({"a": 2.0, "b": [], "c": 1.5, "d": "abc"}, s) == []
    probs = llm.validate({"a": 5, "b": ["x", "z", "y"], "c": "no", "d": "abcd", "e": 1}, s)
    text = " ".join(probs)
    for want in ("$.a: 5 > 3", "more than 2", "$.b[1]", "$.c: expected", "longer than 3", "$.e: not allowed"):
        assert want in text, (want, probs)
    assert llm.validate({"b": []}, s) == ["$.a: missing"]
    assert llm.validate(True, {"type": "integer"})        # a bool is not an integer


# -- calls ----------------------------------------------------------------------------------------------------------------
def test_forced_tool_call_parses_and_sends_the_right_request(server, layer):
    res = llm.call_json("t", system="sys", user="hello", tool=TOOL)
    assert res.ok and res.data == {"word": "a", "n": 2}
    assert (res.provider, res.model) == ("openai", "m1")
    assert res.attempts == [{"model": "m1", "error": None}]
    req = server.requests[-1]
    assert req["tools"] == [{"type": "function", "function": TOOL}]
    assert req["tool_choice"] == {"type": "function", "function": {"name": "report"}}
    assert req["store"] is False and req["max_completion_tokens"] == 50
    assert "modalities" not in req
    assert req["messages"][0] == {"role": "system", "content": "sys"}
    assert req["messages"][1] == {"role": "user", "content": "hello"}
    # usage x prices: text_in = 1000 - 200 audio - 100 cached = 700
    want = (700 * 2.0 + 100 * 1.0 + 200 * 30.0 + 100 * 10.0) / 1e6
    assert res.cost_usd == pytest.approx(want)
    assert res.usage["audio_tokens"] == 200


def test_audio_goes_as_input_audio_with_text_modality(server, layer):
    async def go():
        return await llm.acall_json("t", system="s", user=["timeline", {"type": "text", "text": "prev"}],
                                    tool=TOOL, audio=b"ID3fake-mp3", audio_format="mp3")
    res = asyncio.run(go())
    assert res.ok
    req = server.requests[-1]
    assert req["modalities"] == ["text"]
    parts = req["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "timeline"} and parts[1] == {"type": "text", "text": "prev"}
    assert parts[2]["type"] == "input_audio"
    assert parts[2]["input_audio"]["format"] == "mp3"
    assert base64.b64decode(parts[2]["input_audio"]["data"]) == b"ID3fake-mp3"


@pytest.mark.parametrize("bad", ["invalid", "bad_json", "no_tool", "500"])
def test_a_failure_moves_to_the_next_model(server, layer, bad):
    server.behaviour["m1"] = bad
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert res.ok and res.model == "m2"
    assert res.attempts[0]["model"] == "m1" and res.attempts[0]["error"]
    assert res.attempts[1] == {"model": "m2", "error": None}
    rows = _ledger(layer.ledger)
    assert [r["ok"] for r in rows] == [False, True] and [r["model"] for r in rows] == ["m1", "m2"]
    for r in rows:
        assert set(r) == {"at", "task", "provider", "model", "ok", "latency_ms", "usage", "cost_usd", "error"}
    if bad == "invalid":
        assert "invalid" in rows[0]["error"] and rows[0]["cost_usd"] > 0     # billed, then refused locally


def test_every_model_failing_is_an_answer_not_an_exception(server, layer):
    server.behaviour.update({"m1": "500", "m2": "invalid"})
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert not res.ok and res.data is None and res.error
    assert [a["model"] for a in res.attempts] == ["m1", "m2"]


def test_breaker_skips_a_failing_model(server, layer):
    server.behaviour["m1"] = "500"
    for _ in range(core.BREAKER_FAILS):
        assert llm.call_json("t", system="s", user="u", tool=TOOL).model == "m2"
    n = len(server.requests)
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert res.ok and res.model == "m2"
    assert len(server.requests) == n + 1                       # m1 was not asked
    assert "breaker open" in res.attempts[0]["error"]
    br = llm.status()["breakers"]["openai:m1"]
    assert br["failures"] == core.BREAKER_FAILS and br["open_for_s"] > 0


def test_missing_model_id_is_skipped_after_the_probe(server, layer):
    server.models = ["m2"]
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert res.ok and res.model == "m2"
    assert res.attempts[0] == {"model": "m1", "error": "not in models.list()"}
    assert [r["model"] for r in server.requests] == ["m2"]
    st = llm.status()["tasks"]["t"]
    assert st["probed"] == {"m1": "missing", "m2": "ok"} and st["usable"] == ["m2"]


def test_a_model_that_rejects_tools_is_marked(server, layer):
    server.behaviour["m1"] = "tool_400"
    assert llm.call_json("t", system="s", user="u", tool=TOOL).model == "m2"
    assert llm.status()["tasks"]["t"]["probed"]["m1"] == "no_tools"
    n = len(server.requests)
    llm.call_json("t", system="s", user="u", tool=TOOL)
    assert [r["model"] for r in server.requests[n:]] == ["m2"]


def test_timeout_is_the_task_timeout(server, layer):
    layer(models=("m1",), timeout_s=0.5)
    server.behaviour["m1"] = "slow"
    t = time.monotonic()
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert not res.ok and res.error == "timeout"
    assert time.monotonic() - t < 2.5


def test_no_model_and_no_key(server, layer, monkeypatch):
    res = llm.call_json("nope", system="s", user="u", tool=TOOL)
    assert not res.ok and res.error == "no model configured"
    monkeypatch.delenv("OPENAI_API_KEY")
    n = len(server.requests)
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert not res.ok and "no key for openai" in res.error
    assert len(server.requests) == n
    assert llm.status()["tasks"]["t"]["usable"] == []


def test_key_from_the_env_file(server, layer, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    layer.env.write_text(f"# comment\nOTHER=1\nexport OPENAI_API_KEY=\"{SECRET}\"\n")
    core.configure(config_path=layer.cfg, env_file=layer.env, ledger_path=layer.ledger)
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    assert res.ok
    assert llm.status()["providers"]["openai"]["key"] is True


def test_the_key_never_appears_in_logs_errors_ledger_or_status(server, layer, caplog):
    caplog.set_level(logging.DEBUG)
    server.behaviour.update({"m1": "401", "m2": "401"})
    res = llm.call_json("t", system="s", user="u", tool=TOOL)
    res2 = asyncio.run(llm.acall_json("t", system="s", user="u", tool=TOOL))
    assert not res.ok and not res2.ok
    assert "401" in res.error and "sk-***" in res.error
    blob = " ".join([caplog.text, res.error, res2.error, json.dumps(res.attempts), layer.ledger.read_text(),
                     json.dumps(llm.status())])
    assert SECRET not in blob
    assert SECRET[3:] not in blob


def test_ledger_spend_and_budget(server, layer):
    layer(budgets={"t": 0.004})
    assert not llm.over_budget("t")
    llm.call_json("t", system="s", user="u", tool=TOOL)        # m1: 0.0085
    assert llm.over_budget("t")
    st = llm.status()
    assert st["spend_today_usd"]["t"] == pytest.approx(0.0085, abs=1e-4)
    assert st["calls_today"]["t"] == 1 and st["budgets"] == {"t": 0.004}
    # a restart reads today's spend back from the ledger
    core.configure(config_path=layer.cfg, env_file=layer.env, ledger_path=layer.ledger)
    assert llm.over_budget("t")


def test_bad_edit_keeps_the_last_good_config(server, layer):
    rev = llm.status()["config"]["revision"]
    layer.cfg.write_text("tasks: [this is: not: valid\n")
    llm.reload(force=True)
    st = llm.status()
    assert st["config"]["error"] and st["config"]["revision"] == rev
    assert st["tasks"]["t"]["models"] == ["m1", "m2"]
    assert llm.call_json("t", system="s", user="u", tool=TOOL).ok
    layer.cfg.write_text("tasks:\n  t:\n    models:\n      - {provider: nowhere, model: x}\n")
    llm.reload(force=True)
    assert "unknown provider" in llm.status()["config"]["error"]


def test_task_settings_copy(layer):
    s = llm.task_settings("t")
    assert s["timeout_s"] == 2 and [m["model"] for m in s["models"]] == ["m1", "m2"]
    s["models"].clear()
    assert llm.task_settings("t")["models"]
    assert llm.task_settings("nope") == {}


def test_redact():
    assert llm.redact("key sk-abcdef123456 and sk-proj-XYZ_123") == "key sk-*** and sk-***"
    assert "AIza***" in llm.redact("AIzaSyA1234567890abcdef")
    assert llm.redact("my secret-value-here", ["secret-value-here"]) == "my ***"
