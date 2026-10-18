import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from app.config import get_settings
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
_run_id: ContextVar[str | None] = ContextVar("openai_usage_run_id", default=None)
_step_id: ContextVar[str | None] = ContextVar("openai_usage_step_id", default=None)
_query_id: ContextVar[str | None] = ContextVar("openai_usage_query_id", default=None)
_collector: ContextVar[list[dict] | None] = ContextVar("openai_usage_collector", default=None)


@contextmanager
def openai_usage_context(
    task: str,
    product_id: UUID | str | None = None,
    *,
    run_id: str | None = None,
    collector: list[dict] | None = None,
) -> Iterator[None]:
    """Label OpenAI calls with task/product and optional diagnostic-run context."""
    task_token = _task.set(task)
    product_token = _product_id.set(str(product_id) if product_id is not None else None)
    run_token = _run_id.set(run_id)
    collector_token = _collector.set(collector)
    try:
        yield
    finally:
        _collector.reset(collector_token)
        _run_id.reset(run_token)
        _product_id.reset(product_token)
        _task.reset(task_token)


@contextmanager
def capture_openai_usage_rows(
    rows: list[dict], *, task: str, product_id: UUID | str | None = None
) -> Iterator[None]:
    """Capture a paid subcall without dropping the parent run and usage trace.

    The outer collector must still receive the same provider response once.
    The existing run/step/query contextvars remain unchanged.
    """
    parent = _collector.get()

    class _ForwardingCollector(list):
        def append(self, payload: dict) -> None:
            rows.append(dict(payload))
            if parent is not None:
                parent.append(dict(payload))

    token = _collector.set(_ForwardingCollector())
    task_token = _task.set(task)
    product_token = _product_id.set(
        str(product_id) if product_id is not None else _product_id.get()
    )
    try:
        yield
    finally:
        _product_id.reset(product_token)
        _task.reset(task_token)
        _collector.reset(token)


@contextmanager
def openai_usage_step_context(
    *,
    step_id: str | None = None,
    query_id: str | None = None,
) -> Iterator[None]:
    """Attach one provider call to a run step/query without replacing task context."""
    step_token = _step_id.set(step_id)
    query_token = _query_id.set(query_id)
    try:
        yield
    finally:
        _query_id.reset(query_token)
        _step_id.reset(step_token)


def _task_map(raw: str) -> dict[str, str]:
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {task.strip(): value.strip() for task, value in pairs if task.strip() and value.strip()}


def openai_task_options(default_model: str) -> dict[str, Any]:
    """Model and reasoning for the current task; unconfigured tasks keep today's request."""
    settings = get_settings()
    task = _task.get()
    options: dict[str, Any] = {
        "model": _task_map(settings.openai_task_models).get(task, default_model),
    }
    effort = _task_map(settings.openai_task_reasoning_effort).get(task)
    if effort:
        options["reasoning"] = {"effort": effort}
    return options


def log_openai_usage(
    response: Any,
    *,
    options: dict[str, Any],
    duration_seconds: float,
) -> None:
    """Write one JSON line with the call's token usage and collect it when requested."""
    response_id = None
    try:
        response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        payload = {
            "event": "openai_usage",
            "task": _task.get(),
            "model": options["model"],
            "reasoning_effort": options.get("reasoning", {}).get("effort"),
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
        correlations = {
            "run_id": _run_id.get(),
            "step_id": _step_id.get(),
            "query_id": _query_id.get(),
        }
        payload.update({key: value for key, value in correlations.items() if value is not None})
        collector = _collector.get()
        if collector is not None:
            collector.append(dict(payload))
        _logger.info(json.dumps(payload))
    except Exception as exc:
        # JsonFormatter drops tracebacks, so keep the error itself in the message.
        _logger.exception(
            "openai_usage logging failed task=%s response_id=%s error=%r",
            _task.get(),
            response_id,
            exc,
        )


def summarize_openai_usage(rows: list[dict]) -> dict:
    """Aggregate captured provider usage without inventing a pricing model."""

    def _sum(field: str) -> int:
        return sum(int(row.get(field) or 0) for row in rows)

    return {
        "response_count": len(rows),
        "input_tokens": _sum("input_tokens"),
        "cached_input_tokens": _sum("cached_input_tokens"),
        "output_tokens": _sum("output_tokens"),
        "reasoning_tokens": _sum("reasoning_tokens"),
        "web_search_calls": _sum("web_search_calls"),
        "duration_ms": _sum("duration_ms"),
        "estimated_cost_usd": None,
        "estimated_cost_status": "PRICING_NOT_CONFIGURED",
        "actual_billed_cost_usd": None,
        "actual_billed_cost_status": "NOT_AVAILABLE_FROM_RESPONSE_USAGE",
    }
