import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from app.logging import JsonFormatter

_logger = logging.getLogger(__name__)
# Workers and CLIs never call configure_logging(), so their root logger drops INFO.
# Usage lines are the only per-task spend record, so this logger always writes its own.
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
_logger.addHandler(_handler)
_logger.setLevel(logging.INFO)
_logger.propagate = False

_task: ContextVar[str] = ContextVar("openai_usage_task", default="unknown")
_product_id: ContextVar[str | None] = ContextVar("openai_usage_product_id", default=None)


@contextmanager
def openai_usage_context(task: str, product_id: UUID | str | None = None) -> Iterator[None]:
    """Label every OpenAI call made inside this block with a task and product."""
    task_token = _task.set(task)
    product_token = _product_id.set(str(product_id) if product_id is not None else None)
    try:
        yield
    finally:
        _task.reset(task_token)
        _product_id.reset(product_token)


def log_openai_usage(response: Any, *, model: str, duration_seconds: float) -> None:
    """Write one JSON line with the call's token usage. Never raises."""
    response_id = None
    try:
        response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        payload = {
            "event": "openai_usage",
            "task": _task.get(),
            "model": model,
            "product_id": _product_id.get(),
            "response_id": response_id,
            "input_tokens": getattr(usage, "input_tokens", None),
            "cached_input_tokens": getattr(input_details, "cached_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "reasoning_tokens": getattr(output_details, "reasoning_tokens", None),
            "web_search_calls": sum(
                1
                for item in getattr(response, "output", None) or []
                if getattr(item, "type", None) == "web_search_call"
            ),
            "duration_ms": round(duration_seconds * 1000),
        }
        _logger.info(json.dumps(payload))
    except Exception as exc:
        # JsonFormatter drops tracebacks, so keep the error itself in the message.
        _logger.exception(
            "openai_usage logging failed task=%s response_id=%s error=%r",
            _task.get(),
            response_id,
            exc,
        )
