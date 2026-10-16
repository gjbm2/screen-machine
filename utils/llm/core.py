"""utils.llm: one small layer for JSON-returning LLM calls (docs/SHOW_ENGINE.md §7.6; show_service/README.md, "Stage 3 >
S3.12").

* **Config.** ``config/llm.yaml`` (re-read when its mtime or size changes, polled at most every RELOAD_S): providers
  (``key_env``, ``base_url``), tasks (an ordered model list, ``timeout_s``, ``max_output_tokens``, ``audio``), prices
  (USD per 1M tokens) and daily budgets. Model ids live only there. A bad edit keeps the last good file.
* **One call.** ``call_json`` (sync) / ``acall_json`` (AsyncOpenAI) send one Chat Completions request with one forced
  function tool (``tool_choice`` names it, ``store=False``), optionally with ``input_audio``, parse the tool call's
  arguments and check them with a small local JSON-schema validator. They never raise.
* **Fallback.** The task's models in order. A missing key, an open breaker (BREAKER_FAILS failures in a row skip a
  model for BREAKER_OPEN_S), an id missing from ``models.list()`` (probed at first use, then hourly) or a model that
  rejected tools is skipped. A failure (HTTP error, timeout, no tool call, invalid JSON) moves to the next model. No
  retries: the caller's next window is the retry.
* **The key.** ``key_env`` from the environment, else from the repo's ``.env`` (KEY=VALUE lines). It is never logged
  and never put in an error string, the ledger or ``status()``: every error string goes through ``redact``.
* **The ledger.** One JSON line per request to LEDGER_FILE: ``{at, task, provider, model, ok, latency_ms, usage,
  cost_usd, error}``; cost = usage tokens x llm.yaml's prices (``cost_usd`` null when the model has no price).
  Today's spend per task (local date) is kept in memory, seeded from the ledger at first use.

Works under Flask's ``.venv`` and ``~/.venvs/sm-show`` (openai 1.x, PyYAML; nothing else).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

log = logging.getLogger("utils.llm")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "config" / "llm.yaml"
ENV_FILE = REPO_ROOT / ".env"
# = show_service.contract.LLM_LEDGER (this package never imports show_service)
LEDGER_FILE = Path.home() / ".local" / "state" / "screen-machine" / "llm" / "ledger.jsonl"

RELOAD_S = 2.0               # llm.yaml / .env mtime polls
PROBE_S = 3600.0             # models.list() again after this
PROBE_RETRY_S = 300.0        # after a failed probe (network): the models are tried meanwhile
PROBE_TIMEOUT_S = 10.0
BREAKER_FAILS = 3            # failures in a row...
BREAKER_OPEN_S = 60.0        # ...skip that model this long
LEDGER_ROTATE_BYTES = 5_000_000
DEFAULT_TIMEOUT_S = 12.0     # a task that names no timeout_s
DEFAULT_MAX_OUTPUT_TOKENS = 400
ERROR_MAX = 300              # characters of an error string
DEFAULT_PROVIDERS: dict[str, dict] = {"openai": {"key_env": "OPENAI_API_KEY", "base_url": None}}

_KEY_RE = re.compile(r"(sk-|sk_)[A-Za-z0-9_\-*]{3,}")
_AIZA_RE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")


@dataclass
class LLMResult:
    ok: bool
    data: Optional[dict]
    error: Optional[str]
    task: str
    provider: Optional[str]
    model: Optional[str]
    latency_ms: float
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    attempts: list = field(default_factory=list)      # [{"model", "error"}] in order; error None on the one that won


# -- validation (no dependency: type, required, enum, properties, items, maxItems, minItems, minimum, maximum,
#    maxLength, minLength, additionalProperties false) -------------------------------------------------------------
def _is_type(v: Any, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return True                                         # an unknown type word: not ours to refuse


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The problems with ``value`` against ``schema`` (empty: valid)."""
    if not isinstance(schema, dict):
        return []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return [f"{path}: expected {'|'.join(types)}, got {type(value).__name__}"]
    out: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in enum")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for r in schema.get("required") or ():
            if r not in value:
                out.append(f"{path}.{r}: missing")
        for k, v in value.items():
            if k in props:
                out.extend(validate(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}.{k}: not allowed")
    elif isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, v in enumerate(value):
                out.extend(validate(v, items, f"{path}[{i}]"))
    elif isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > {schema['maximum']}")
    return out


# -- the layer -------------------------------------------------------------------------------------------------------
def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _parse_config(raw: Any) -> dict:
    """llm.yaml -> the resolved config; ValueError on a shape that can't be used (the last good one stays)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("llm.yaml: the top level must be a mapping")
    provs = raw.get("providers")
    providers: dict[str, dict] = {k: dict(v) for k, v in DEFAULT_PROVIDERS.items()}
    if provs is not None:
        if not isinstance(provs, dict):
            raise ValueError("providers: must be a mapping")
        for name, p in provs.items():
            if not isinstance(p, dict) or not isinstance(p.get("key_env"), str):
                raise ValueError(f"providers.{name}: needs key_env (the name of the variable holding the key)")
            base = p.get("base_url")
            if base is not None and not isinstance(base, str):
                raise ValueError(f"providers.{name}.base_url: a URL or null")
            providers[str(name)] = {"key_env": p["key_env"], "base_url": base or None,
                                    "omit": [str(x) for x in (p.get("omit") or [])]}
    tasks: dict[str, dict] = {}
    for name, t in (raw.get("tasks") or {}).items():
        if not isinstance(t, dict):
            raise ValueError(f"tasks.{name}: must be a mapping")
        models = []
        for i, m in enumerate(t.get("models") or []):
            if not isinstance(m, dict) or not isinstance(m.get("model"), str) or not m["model"]:
                raise ValueError(f"tasks.{name}.models[{i}]: needs {{provider, model}}")
            prov = str(m.get("provider") or "openai")
            if prov not in providers:
                raise ValueError(f"tasks.{name}.models[{i}]: unknown provider {prov!r}")
            opts = m.get("options") or {}
            if not isinstance(opts, dict):
                raise ValueError(f"tasks.{name}.models[{i}].options: must be a mapping")
            models.append({"provider": prov, "model": m["model"], "options": dict(opts)})
        audio = t.get("audio", False)
        if not isinstance(audio, (bool, dict)):
            raise ValueError(f"tasks.{name}.audio: true/false or a mapping")
        tasks[str(name)] = {"models": models,
                            "timeout_s": _num(t.get("timeout_s"), DEFAULT_TIMEOUT_S),
                            "max_output_tokens": int(_num(t.get("max_output_tokens"), DEFAULT_MAX_OUTPUT_TOKENS)),
                            "audio": audio}
    prices: dict[str, dict] = {}
    for model, p in (raw.get("prices") or {}).items():
        if not isinstance(p, dict):
            raise ValueError(f"prices.{model}: must be a mapping")
        q = {k: _num(v) for k, v in p.items()}
        if q.get("text_in") is None or q.get("text_out") is None:
            raise ValueError(f"prices.{model}: needs text_in and text_out (USD per 1M tokens)")
        prices[str(model)] = q
    budgets: dict[str, float] = {}
    for task, b in (raw.get("budgets") or {}).items():
        v = _num(b)
        if v is None or v < 0:
            raise ValueError(f"budgets.{task}: a number of USD per day")
        budgets[str(task)] = v
    return {"providers": providers, "tasks": tasks, "prices": prices, "budgets": budgets}


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, sep, v = line.partition("=")
        if not sep:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[k.strip()] = v
    return out


def _local_day(epoch: Optional[float] = None) -> str:
    return _dt.datetime.fromtimestamp(time.time() if epoch is None else epoch).strftime("%Y-%m-%d")


class _Layer:
    """Process-wide state; every method is thread-safe (one lock, held only for dict work, never across I/O)."""

    def __init__(self, config_path: Path = CONFIG_FILE, env_file: Path = ENV_FILE,
                 ledger_path: Path = LEDGER_FILE) -> None:
        self.config_path, self.env_file, self.ledger_path = Path(config_path), Path(env_file), Path(ledger_path)
        self.lock = threading.RLock()
        self.cfg: dict = _parse_config({})
        self.revision: Optional[str] = None
        self.cfg_error: Optional[str] = None
        self._cfg_sig: Any = None
        self._cfg_poll = 0.0
        self._env: dict[str, str] = {}
        self._env_sig: Any = None
        self._env_poll = 0.0
        self._keys_seen: set[str] = set()             # every key value read: redacted wherever it might appear
        self._clients: dict[tuple, Any] = {}
        self._aclients: "weakref.WeakKeyDictionary[Any, dict]" = weakref.WeakKeyDictionary()
        self._probes: dict[str, dict] = {}            # provider -> {"at", "ids": set | None, "error"}
        self.probed: dict[str, str] = {}              # "provider:model" -> ok | missing | no_tools
        self.breakers: dict[str, dict] = {}           # "provider:model" -> {"fails", "open_until"}
        self._once: set[str] = set()
        self._day: Optional[str] = None
        self.spend: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self._ledger_seeded = False
        self.reload(force=True)

    # -- config and keys ---------------------------------------------------------------------------------------
    def reload(self, force: bool = False) -> None:
        now = time.monotonic()
        with self.lock:
            if not force and now - self._cfg_poll < RELOAD_S:
                return
            self._cfg_poll = now
        try:
            st = self.config_path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        with self.lock:
            if not force and sig == self._cfg_sig:
                return
            self._cfg_sig = sig
        if sig is None:
            with self.lock:
                self.cfg, self.revision = _parse_config({}), None
                self.cfg_error = f"{self.config_path} not found: no model configured"
            self._log_once("noconfig", logging.WARNING, "%s not found: no model configured", self.config_path)
            return
        try:
            text = self.config_path.read_text()
            import yaml                               # PyYAML: in both venvs
            cfg = _parse_config(yaml.safe_load(text))
        except Exception as e:                        # noqa: BLE001 - keep the last good file
            with self.lock:
                self.cfg_error = redact(f"{type(e).__name__}: {e}", self._keys_seen)
            log.error("llm.yaml not loaded (%s): keeping the last good settings", self.cfg_error)
            return
        with self.lock:
            self.cfg = cfg
            self.revision = hashlib.sha1(text.encode()).hexdigest()[:12]
            self.cfg_error = None
            self.probed = {k: v for k, v in self.probed.items() if v != "no_tools"}
        log.info("llm.yaml loaded (revision %s): tasks %s", self.revision,
                 ", ".join(f"{t}={'/'.join(m['model'] for m in c['models']) or 'none'}"
                           for t, c in cfg["tasks"].items()) or "none")

    def key(self, provider: str) -> Optional[str]:
        p = self.cfg["providers"].get(provider)
        if p is None:
            return None
        name = p["key_env"]
        v = os.environ.get(name)
        if not v:
            v = self._env_file().get(name)
        if v:
            with self.lock:
                self._keys_seen.add(v)
        return v or None

    def _env_file(self) -> dict[str, str]:
        now = time.monotonic()
        if now - self._env_poll < RELOAD_S and self._env_sig is not None:
            return self._env
        self._env_poll = now
        try:
            st = self.env_file.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._env, self._env_sig = {}, ("missing",)
            return self._env
        if sig != self._env_sig:
            try:
                env = _parse_env(self.env_file.read_text())
            except OSError:
                env = {}
            with self.lock:
                self._env, self._env_sig = env, sig
        return self._env

    def _log_once(self, tag: str, level: int, msg: str, *args: Any) -> None:
        with self.lock:
            if tag in self._once:
                return
            self._once.add(tag)
        log.log(level, msg, *args)

    # -- clients -----------------------------------------------------------------------------------------------
    def _client_key(self, provider: str, key: str) -> tuple:
        base = self.cfg["providers"][provider]["base_url"]
        return (provider, base, hashlib.sha256(key.encode()).hexdigest()[:16])

    def client(self, provider: str, key: str) -> Any:
        ck = self._client_key(provider, key)
        with self.lock:
            c = self._clients.get(ck)
        if c is None:
            from openai import OpenAI
            c = OpenAI(api_key=key, base_url=ck[1], max_retries=0)
            with self.lock:
                self._clients[ck] = c
        return c

    def aclient(self, provider: str, key: str) -> Any:
        loop = asyncio.get_running_loop()
        ck = self._client_key(provider, key)
        with self.lock:
            per = self._aclients.get(loop)
            if per is None:
                per = self._aclients[loop] = {}
            c = per.get(ck)
        if c is None:
            from openai import AsyncOpenAI
            c = AsyncOpenAI(api_key=key, base_url=ck[1], max_retries=0)
            with self.lock:
                per[ck] = c
        return c

    # -- probes --------------------------------------------------------------------------------------------------
    def probe_due(self, provider: str) -> bool:
        with self.lock:
            p = self._probes.get(provider)
        if p is None:
            return True
        wait = PROBE_S if p.get("ids") is not None else PROBE_RETRY_S
        return time.monotonic() - p["at"] >= wait

    def probe_done(self, provider: str, ids: Optional[set], error: Optional[str]) -> None:
        with self.lock:
            self._probes[provider] = {"at": time.monotonic(), "ids": ids, "error": error}
            if ids is None:
                return
            for t in self.cfg["tasks"].values():
                for m in t["models"]:
                    if m["provider"] != provider:
                        continue
                    mk = f"{provider}:{m['model']}"
                    self.probed[mk] = "ok" if m["model"] in ids else "missing"
        if error:
            log.warning("models.list() for %s failed (%s): trying the models anyway", provider, error)
        else:
            for t in self.cfg["tasks"].values():
                for m in t["models"]:
                    if m["provider"] == provider and m["model"] not in (ids or ()):
                        self._log_once(f"missing:{provider}:{m['model']}", logging.ERROR,
                                       "llm.yaml lists %s, but %s's models.list() doesn't have it: skipped",
                                       m["model"], provider)

    # -- planning, accounting --------------------------------------------------------------------------------------
    def task(self, name: str) -> Optional[dict]:
        return self.cfg["tasks"].get(name)

    def skip_reason(self, provider: str, model: str) -> Optional[str]:
        mk = f"{provider}:{model}"
        if provider not in self.cfg["providers"]:
            return f"unknown provider {provider}"
        if self.key(provider) is None:
            return f"no key for {provider} ({self.cfg['providers'][provider]['key_env']})"
        with self.lock:
            b = self.breakers.get(mk)
            state = self.probed.get(mk)
        if b and b["open_until"] > time.monotonic():
            return f"breaker open ({b['fails']} failures in a row)"
        if state == "missing":
            return "not in models.list()"
        if state == "no_tools":
            return "rejected the forced tool"
        return None

    def failed(self, provider: str, model: str, error: str, status: Optional[int] = None) -> None:
        mk = f"{provider}:{model}"
        with self.lock:
            b = self.breakers.setdefault(mk, {"fails": 0, "open_until": 0.0})
            b["fails"] += 1
            if b["fails"] >= BREAKER_FAILS:
                b["open_until"] = time.monotonic() + BREAKER_OPEN_S
            if status == 404 and "model" in error.lower():
                self.probed[mk] = "missing"
            elif status == 400 and "tool" in error.lower():
                self.probed[mk] = "no_tools"
        if status == 404 or (status == 400 and "tool" in error.lower()):
            self._log_once(f"bad:{mk}:{status}", logging.ERROR, "%s (%s): %s; skipped from now on", model, provider,
                           error)

    def succeeded(self, provider: str, model: str) -> None:
        with self.lock:
            self.breakers.pop(f"{provider}:{model}", None)
            self.probed.setdefault(f"{provider}:{model}", "ok")

    def cost(self, model: str, usage: dict) -> Optional[float]:
        p = self.cfg["prices"].get(model)
        if p is None:
            if usage:
                self._log_once(f"noprice:{model}", logging.WARNING,
                               "llm.yaml has no price for %s: its calls count as $0 in the budget", model)
            return None
        prompt = int(usage.get("prompt_tokens") or 0)
        audio = int(usage.get("audio_tokens") or 0)
        cached = int(usage.get("cached_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        text_in = max(0, prompt - audio - cached)
        ti, to = p["text_in"], p["text_out"]
        usd = (text_in * ti + cached * (p.get("cached_in") if p.get("cached_in") is not None else ti)
               + audio * (p.get("audio_in") if p.get("audio_in") is not None else ti) + out * to) / 1e6
        return round(usd, 6)

    def _roll_day(self) -> None:
        day = _local_day()
        if day != self._day:
            self._day, self.spend, self.calls = day, {}, {}

    def _seed_from_ledger(self) -> None:
        """Today's spend from the ledger (once per process), so a restart doesn't reset the budget."""
        with self.lock:
            if self._ledger_seeded:
                return
            self._ledger_seeded = True
            self._roll_day()
            day = self._day
        try:
            if not self.ledger_path.exists() or self.ledger_path.stat().st_size > LEDGER_ROTATE_BYTES * 2:
                return
            spend: dict[str, float] = {}
            calls: dict[str, int] = {}
            with self.ledger_path.open() as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if _local_day(float(e.get("at") or 0)) != day:
                        continue
                    t = str(e.get("task"))
                    calls[t] = calls.get(t, 0) + 1
                    spend[t] = spend.get(t, 0.0) + float(e.get("cost_usd") or 0.0)
            with self.lock:
                if self._day == day:
                    for t, v in spend.items():
                        self.spend[t] = self.spend.get(t, 0.0) + v
                    for t, n in calls.items():
                        self.calls[t] = self.calls.get(t, 0) + n
        except Exception as e:                        # noqa: BLE001 - the budget starts at 0 instead
            log.warning("ledger not read (%s): today's spend starts at 0", e)

    def account(self, task: str, cost: Optional[float]) -> None:
        self._seed_from_ledger()
        with self.lock:
            self._roll_day()
            self.calls[task] = self.calls.get(task, 0) + 1
            self.spend[task] = self.spend.get(task, 0.0) + float(cost or 0.0)

    def spend_today(self, task: str) -> float:
        self._seed_from_ledger()
        with self.lock:
            self._roll_day()
            return self.spend.get(task, 0.0)

    def ledger(self, task: str, provider: str, model: str, ok: bool, latency_ms: float, usage: dict,
               cost: Optional[float], error: Optional[str]) -> None:
        line = json.dumps({"at": round(time.time(), 3), "task": task, "provider": provider, "model": model, "ok": ok,
                           "latency_ms": round(latency_ms, 1), "usage": usage, "cost_usd": cost,
                           "error": redact(error, self._keys_seen) if error else None}, separators=(",", ":"))
        try:
            with self.lock:
                d = self.ledger_path.parent
                d.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    if self.ledger_path.stat().st_size > LEDGER_ROTATE_BYTES:
                        os.replace(self.ledger_path, self.ledger_path.with_suffix(".jsonl.1"))
                except OSError:
                    pass
                fd = os.open(self.ledger_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a") as fh:
                    fh.write(line + "\n")
        except Exception as e:                        # noqa: BLE001 - never fails a call
            self._log_once("ledger", logging.ERROR, "LLM ledger not written (%s)", e)

    def status(self) -> dict:
        self._seed_from_ledger()
        with self.lock:
            self._roll_day()
            cfg = self.cfg
            now = time.monotonic()
            tasks = {}
            for name, t in cfg["tasks"].items():
                ids = [m["model"] for m in t["models"]]
                probed = {m["model"]: self.probed[f"{m['provider']}:{m['model']}"]
                          for m in t["models"] if f"{m['provider']}:{m['model']}" in self.probed}
                tasks[name] = {"models": ids, "probed": probed, "timeout_s": t["timeout_s"]}
            breakers = {mk: {"failures": b["fails"], "open_for_s": round(max(0.0, b["open_until"] - now), 1)}
                        for mk, b in self.breakers.items()}
            probes = {p: {"age_s": round(now - v["at"], 1), "ok": v["ids"] is not None, "error": v["error"]}
                      for p, v in self._probes.items()}
            out = {"config": {"path": str(self.config_path), "revision": self.revision, "error": self.cfg_error},
                   "tasks": tasks, "breakers": breakers,
                   "spend_today_usd": {k: round(v, 4) for k, v in self.spend.items()},
                   "calls_today": dict(self.calls), "budgets": dict(cfg["budgets"]), "probes": probes}
        for name, t in tasks.items():
            t["usable"] = [m["model"] for m in cfg["tasks"][name]["models"]
                           if self.skip_reason(m["provider"], m["model"]) is None]
        out["providers"] = {p: {"key": self.key(p) is not None, "base_url": v["base_url"]}
                            for p, v in cfg["providers"].items()}
        return out


_L: Optional[_Layer] = None
_L_LOCK = threading.Lock()


def _layer() -> _Layer:
    global _L
    if _L is None:
        with _L_LOCK:
            if _L is None:
                _L = _Layer()
    return _L


def configure(*, config_path: Optional[Path] = None, env_file: Optional[Path] = None,
              ledger_path: Optional[Path] = None) -> None:
    """Point the layer at other files (tests, tools) and start fresh: clients, probes, breakers, spend."""
    global _L
    with _L_LOCK:
        old, _L = _L, _Layer(config_path or CONFIG_FILE, env_file or ENV_FILE, ledger_path or LEDGER_FILE)
    if old is not None:
        for c in list(old._clients.values()):
            try:
                c.close()
            except Exception:                         # noqa: BLE001
                pass


# -- helpers ---------------------------------------------------------------------------------------------------------
def redact(text: Any, keys: Any = ()) -> str:
    s = _KEY_RE.sub(lambda m: m.group(1) + "***", str(text))
    s = _AIZA_RE.sub("AIza***", s)
    for k in list(keys or ()):
        if k and len(k) >= 6:
            s = s.replace(k, "***")
    return s[:ERROR_MAX]


def _error_of(e: BaseException, keys: Any) -> tuple[str, Optional[int]]:
    status = getattr(e, "status_code", None)
    name = type(e).__name__
    if name in ("APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout") or isinstance(
            e, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", None
    if name == "APIConnectionError":
        return redact(f"connection: {e}", keys), None
    msg = getattr(e, "message", None) or str(e)
    if status is not None:
        return redact(f"http {status}: {msg}", keys), int(status)
    return redact(f"{name}: {msg}", keys), None


def _messages(system: str, user: Union[str, list], audio: Optional[bytes], audio_format: str) -> list[dict]:
    if isinstance(user, str) and audio is None:
        content: Any = user
    else:
        parts: list[dict] = []
        for p in ([user] if isinstance(user, str) else list(user or [])):
            if isinstance(p, str):
                parts.append({"type": "text", "text": p})
            elif isinstance(p, dict):
                parts.append(p)
        if audio is not None:
            parts.append({"type": "input_audio",
                          "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": audio_format}})
        content = parts
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def _request(L: _Layer, entry: dict, task_cfg: dict, messages: list, tool: dict, audio: bool,
             timeout_s: float) -> dict:
    fn = {k: tool[k] for k in ("name", "description", "parameters") if k in tool}
    kw: dict[str, Any] = {"model": entry["model"], "messages": messages,
                          "tools": [{"type": "function", "function": fn}],
                          "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
                          "store": False, "max_completion_tokens": task_cfg["max_output_tokens"],
                          "timeout": timeout_s}
    if audio:
        kw["modalities"] = ["text"]
    kw.update(entry.get("options") or {})
    for k in L.cfg["providers"][entry["provider"]].get("omit") or ():
        kw.pop(k, None)
    return kw


def _usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    pd = getattr(u, "prompt_tokens_details", None)
    cd = getattr(u, "completion_tokens_details", None)
    out = {"prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
           "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
           "audio_tokens": int(getattr(pd, "audio_tokens", 0) or 0) if pd is not None else 0,
           "cached_tokens": int(getattr(pd, "cached_tokens", 0) or 0) if pd is not None else 0}
    r = int(getattr(cd, "reasoning_tokens", 0) or 0) if cd is not None else 0
    if r:
        out["reasoning_tokens"] = r
    return out


def _parse(resp: Any, tool: dict) -> tuple[Optional[dict], Optional[str]]:
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return None, "no choices in the answer"
    args = None
    for c in (getattr(msg, "tool_calls", None) or []):
        f = getattr(c, "function", None)
        if f is not None and (args is None or f.name == tool["name"]):
            args = f.arguments
            if f.name == tool["name"]:
                break
    if args is None:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip().startswith("{"):
            args = content
        else:
            return None, "no tool call in the answer"
    try:
        data = json.loads(args) if isinstance(args, str) else args
    except ValueError as e:
        return None, f"tool arguments are not JSON ({e})"
    if not isinstance(data, dict):
        return None, "tool arguments are not an object"
    problems = validate(data, tool.get("parameters") or {})
    if problems:
        return None, "invalid: " + "; ".join(problems[:5])
    return data, None


def _fail_result(task: str, t0: float, error: str, attempts: list, cost: float = 0.0) -> LLMResult:
    return LLMResult(False, None, error, task, None, None, (time.perf_counter() - t0) * 1000.0, {}, cost, attempts)


def _start(task: str, timeout_s: Optional[float]) -> tuple[_Layer, Optional[dict], float]:
    L = _layer()
    L.reload()
    tcfg = L.task(task)
    return L, tcfg, float(timeout_s if timeout_s is not None else (tcfg or {}).get("timeout_s") or DEFAULT_TIMEOUT_S)


def call_json(task: str, *, system: str, user: Union[str, list], tool: dict, audio: Optional[bytes] = None,
              audio_format: str = "mp3", timeout_s: Optional[float] = None) -> LLMResult:
    """One forced-tool call for ``task`` (sync; Flask threads, tools). Never raises."""
    t0 = time.perf_counter()
    attempts: list[dict] = []
    total = 0.0
    try:
        L, tcfg, tmo = _start(task, timeout_s)
        if not tcfg or not tcfg["models"]:
            return _fail_result(task, t0, "no model configured", attempts)
        messages = _messages(system, user, audio, audio_format)
        for entry in tcfg["models"]:
            prov, model = entry["provider"], entry["model"]
            why = L.skip_reason(prov, model)
            if why is None and L.probe_due(prov):
                key = L.key(prov)
                try:
                    page = L.client(prov, key).models.list(timeout=PROBE_TIMEOUT_S)
                    L.probe_done(prov, {m.id for m in page.data}, None)
                except Exception as e:                # noqa: BLE001
                    L.probe_done(prov, None, _error_of(e, L._keys_seen)[0])
                why = L.skip_reason(prov, model)
            if why is not None:
                attempts.append({"model": model, "error": why})
                continue
            kw = _request(L, entry, tcfg, messages, tool, audio is not None, tmo)
            t = time.perf_counter()
            try:
                resp = L.client(prov, L.key(prov)).chat.completions.create(**kw)
            except Exception as e:                    # noqa: BLE001
                err, status = _error_of(e, L._keys_seen)
                ms = (time.perf_counter() - t) * 1000.0
                L.failed(prov, model, err, status)
                L.account(task, None)
                L.ledger(task, prov, model, False, ms, {}, None, err)
                log.warning("%s: %s failed in %.0f ms: %s", task, model, ms, err)
                attempts.append({"model": model, "error": err})
                continue
            res = _finish(L, task, entry, resp, tool, t, attempts)
            total += res.cost_usd
            if res.ok:
                res.cost_usd = round(total, 6)
                return res
        return _fail_result(task, t0, attempts[-1]["error"] if attempts else "no model configured", attempts,
                            round(total, 6))
    except Exception as e:                            # noqa: BLE001 - never raises
        log.exception("utils.llm internal error")
        return _fail_result(task, t0, redact(f"internal: {type(e).__name__}: {e}"), attempts, total)


def _finish(L: _Layer, task: str, entry: dict, resp: Any, tool: dict, t: float, attempts: list) -> LLMResult:
    prov, model = entry["provider"], entry["model"]
    ms = (time.perf_counter() - t) * 1000.0
    usage = _usage(resp)
    cost = L.cost(model, usage)
    L.account(task, cost)
    data, err = _parse(resp, tool)
    if err is not None:
        L.failed(prov, model, err)
        L.ledger(task, prov, model, False, ms, usage, cost, err)
        log.warning("%s: %s answered in %.0f ms but %s", task, model, ms, redact(err, L._keys_seen))
        attempts.append({"model": model, "error": err})
        return LLMResult(False, None, err, task, prov, model, ms, usage, float(cost or 0.0), attempts)
    L.succeeded(prov, model)
    L.ledger(task, prov, model, True, ms, usage, cost, None)
    attempts.append({"model": model, "error": None})
    return LLMResult(True, data, None, task, prov, model, ms, usage, float(cost or 0.0), attempts)


async def acall_json(task: str, *, system: str, user: Union[str, list], tool: dict, audio: Optional[bytes] = None,
                     audio_format: str = "mp3", timeout_s: Optional[float] = None) -> LLMResult:
    """call_json on AsyncOpenAI (show-brain's loop): the same rules. Never raises (a cancel still propagates)."""
    t0 = time.perf_counter()
    attempts: list[dict] = []
    total = 0.0
    try:
        L, tcfg, tmo = _start(task, timeout_s)
        if not tcfg or not tcfg["models"]:
            return _fail_result(task, t0, "no model configured", attempts)
        messages = _messages(system, user, audio, audio_format)
        for entry in tcfg["models"]:
            prov, model = entry["provider"], entry["model"]
            why = L.skip_reason(prov, model)
            if why is None and L.probe_due(prov):
                try:
                    page = await L.aclient(prov, L.key(prov)).models.list(timeout=PROBE_TIMEOUT_S)
                    L.probe_done(prov, {m.id for m in page.data}, None)
                except asyncio.CancelledError:
                    raise
                except Exception as e:                # noqa: BLE001
                    L.probe_done(prov, None, _error_of(e, L._keys_seen)[0])
                why = L.skip_reason(prov, model)
            if why is not None:
                attempts.append({"model": model, "error": why})
                continue
            kw = _request(L, entry, tcfg, messages, tool, audio is not None, tmo)
            t = time.perf_counter()
            try:
                resp = await L.aclient(prov, L.key(prov)).chat.completions.create(**kw)
            except asyncio.CancelledError:
                raise
            except Exception as e:                    # noqa: BLE001
                err, status = _error_of(e, L._keys_seen)
                ms = (time.perf_counter() - t) * 1000.0
                L.failed(prov, model, err, status)
                L.account(task, None)
                L.ledger(task, prov, model, False, ms, {}, None, err)
                log.warning("%s: %s failed in %.0f ms: %s", task, model, ms, err)
                attempts.append({"model": model, "error": err})
                continue
            res = _finish(L, task, entry, resp, tool, t, attempts)
            total += res.cost_usd
            if res.ok:
                res.cost_usd = round(total, 6)
                return res
        return _fail_result(task, t0, attempts[-1]["error"] if attempts else "no model configured", attempts,
                            round(total, 6))
    except asyncio.CancelledError:
        raise
    except Exception as e:                            # noqa: BLE001 - never raises
        log.exception("utils.llm internal error")
        return _fail_result(task, t0, redact(f"internal: {type(e).__name__}: {e}"), attempts, total)


def status() -> dict:
    """{"config": {"path", "revision", "error"}, "tasks": {task: {"models", "usable", "probed": {id: ok | missing |
    no_tools}, "timeout_s"}}, "breakers": {"provider:id": {"failures", "open_for_s"}}, "spend_today_usd": {task: x},
    "calls_today": {task: n}, "budgets": {task: x}, "probes": {provider: {"age_s", "ok", "error"}},
    "providers": {name: {"key": bool, "base_url"}}}. Never contains a key."""
    try:
        L = _layer()
        L.reload()
        return L.status()
    except Exception as e:                            # noqa: BLE001
        return {"config": {"path": str(CONFIG_FILE), "revision": None, "error": redact(e)}, "tasks": {},
                "breakers": {}, "spend_today_usd": {}, "calls_today": {}, "budgets": {}, "probes": {},
                "providers": {}}


def task_settings(task: str) -> dict:
    """A copy of the task's resolved settings ({"models": [{provider, model, options}], "timeout_s",
    "max_output_tokens", "audio"}), or {} when llm.yaml has no such task."""
    try:
        L = _layer()
        L.reload()
        t = L.task(task)
        return json.loads(json.dumps(t)) if t else {}
    except Exception:                                 # noqa: BLE001
        return {}


def over_budget(task: str) -> bool:
    """Today's spend for ``task`` has reached llm.yaml's budget (none set: never)."""
    try:
        L = _layer()
        L.reload()
        b = L.cfg["budgets"].get(task)
        return b is not None and L.spend_today(task) >= b
    except Exception:                                 # noqa: BLE001
        return False


def reload(force: bool = False) -> None:
    try:
        _layer().reload(force=force)
    except Exception:                                 # noqa: BLE001
        log.exception("llm.yaml reload failed")
