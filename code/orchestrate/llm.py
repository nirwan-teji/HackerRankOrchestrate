"""Provider-agnostic LLM access: swap models globally via env vars.

Features: init_chat_model factory, on-disk response cache (determinism + free
re-runs), RPM throttling, retries, token/cost accounting for the ops report.
"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import time
from typing import Optional, Type, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, messages_to_dict
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import get_settings

T = TypeVar("T", bound=BaseModel)


class Usage:
    """Process-wide counters, dumped by `main.py` as the operational analysis."""

    def __init__(self):
        self.calls = self.cache_hits = self.input_tokens = self.output_tokens = 0
        self.started = time.time()
        self.lock = threading.Lock()

    def add(self, msg) -> None:
        meta = getattr(msg, "usage_metadata", None) or {}
        with self.lock:
            self.calls += 1
            self.input_tokens += meta.get("input_tokens", 0)
            self.output_tokens += meta.get("output_tokens", 0)

    def report(self) -> dict:
        return {
            "model_calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "runtime_sec": round(time.time() - self.started, 1),
        }


USAGE = Usage()
_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle(rpm: int) -> None:
    if rpm <= 0:
        return
    gap = 60.0 / rpm
    with _throttle_lock:
        wait = _last_call[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def get_model(vision: bool = False):
    s = get_settings()
    provider, model = (s.vision_provider, s.vision_model) if vision else (s.provider, s.model)
    return init_chat_model(model, model_provider=provider, temperature=s.temperature)


def _key(kind: str, model: str, payload) -> str:
    raw = json.dumps([kind, model, payload], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(key: str):
    d = get_settings().cache_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _transient(e: BaseException) -> bool:
    """Retry rate limits, timeouts and 5xx; never retry a 400-class request error (it will fail identically)."""
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(code, int):
        return code in (408, 409, 429) or code >= 500
    return isinstance(e, (TimeoutError, ConnectionError)) or type(e).__name__ in ("APIConnectionError", "APITimeoutError", "RateLimitError")


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), retry=retry_if_exception(_transient), reraise=True)
def _invoke(runnable, messages):
    _throttle(get_settings().rpm)
    return runnable.invoke(messages)


# Structured output modes, best first. Open-weight models on hosted APIs differ: e.g. Groq's gpt-oss rejects LangChain's
# default function-calling ("attempted to call tool 'json' which was not in request.tools") but accepts json_schema.
_METHODS = ("json_schema", "json_mode", "function_calling")
_WORKING: dict[str, str] = {}  # model name -> method that worked (remembered for the rest of the process)


def _json_hint(schema) -> str:
    return "\n\nReply with ONLY a JSON object (no prose, no code fences) that matches this JSON Schema:\n" + json.dumps(schema.model_json_schema())


def _parse_text_json(text: str, schema):
    text = text if isinstance(text, str) else str(text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return schema.model_validate_json(m.group(0))


def _structured(llm, schema, messages, model_name: str):
    """Return a validated `schema` instance using the first mode the provider supports; plain-JSON parsing is the last resort."""
    order = list(_METHODS)
    if _WORKING.get(model_name) in order:
        order.remove(_WORKING[model_name])
        order.insert(0, _WORKING[model_name])
    plain_first = _WORKING.get(model_name) == "plain_json"
    hinted = [SystemMessage(content=messages[0].content + _json_hint(schema)), *messages[1:]]
    last: BaseException | None = None
    for method in ([] if plain_first else order):
        try:
            msgs = hinted if method == "json_mode" else messages  # json_mode needs the schema spelled out in the prompt
            r = _invoke(llm.with_structured_output(schema, method=method, include_raw=True), msgs)
            USAGE.add(r.get("raw"))
            if r.get("parsed") is not None:
                _WORKING[model_name] = method
                return r["parsed"]
            last = ValueError(f"{method}: reply did not parse")
        except Exception as e:  # unsupported mode, 400, schema rejection ... -> try the next one
            last = e
    try:  # last resort: ask for JSON in plain text and parse it ourselves
        msg = _invoke(llm, hinted)
        USAGE.add(msg)
        out = _parse_text_json(msg.content, schema)
        _WORKING[model_name] = "plain_json"
        return out
    except Exception as e:
        raise RuntimeError(f"structured output failed for {model_name} in every mode; last error: {last or e}") from e


def ask(
    system: str,
    user: str | list,
    schema: Optional[Type[T]] = None,
    vision: bool = False,
):
    """Single LLM call. `user` may be a str or a LangChain multimodal content list.

    Returns a validated `schema` instance if given, else the text string.
    """
    s = get_settings()
    model_name = s.vision_model if vision else s.model
    messages: list[BaseMessage] = [SystemMessage(content=system), HumanMessage(content=user)]
    key = _key("ask", model_name, [messages_to_dict(messages), schema.model_json_schema() if schema else None])
    path = _cache_path(key) if s.cache else None
    if path and path.exists():
        USAGE.cache_hits += 1
        data = json.loads(path.read_text())
        return schema.model_validate(data) if schema else data["text"]

    llm = get_model(vision)
    if schema:
        out = _structured(llm, schema, messages, model_name)
        if path:
            path.write_text(out.model_dump_json())
        return out
    msg = _invoke(llm, messages)
    USAGE.add(msg)
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    if path:
        path.write_text(json.dumps({"text": text}))
    return text
