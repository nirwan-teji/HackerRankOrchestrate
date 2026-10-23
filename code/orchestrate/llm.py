"""Provider-agnostic LLM access: swap models globally via env vars.

Features: init_chat_model factory, on-disk response cache (determinism + free
re-runs), RPM throttling, retries, token/cost accounting for the ops report.
"""
import hashlib
import json
import threading
import time
from typing import Optional, Type, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, messages_to_dict
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

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


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60), reraise=True)
def _invoke(runnable, messages):
    _throttle(get_settings().rpm)
    return runnable.invoke(messages)


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
        result = _invoke(llm.with_structured_output(schema, include_raw=True), messages)
        USAGE.add(result.get("raw"))
        parsed = result.get("parsed")
        if parsed is None:  # one repair attempt
            raw = result["raw"].content
            fix = [*messages, HumanMessage(content=f"Your last reply was not valid JSON for the schema. Reply with ONLY valid JSON. Previous: {raw}")]
            result = _invoke(llm.with_structured_output(schema, include_raw=True), fix)
            USAGE.add(result.get("raw"))
            parsed = result["parsed"]
        out = parsed
        if path:
            path.write_text(out.model_dump_json())
        return out
    msg = _invoke(llm, messages)
    USAGE.add(msg)
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    if path:
        path.write_text(json.dumps({"text": text}))
    return text
