from types import SimpleNamespace

import pytest

from app.discovery_execution_context import DiscoveryExecutionContext, DiscoveryExecutionMode
from app.manual_discovery_trace import _TracingSearchProvider
from app.openai_usage import (
    log_openai_usage,
    openai_usage_context,
    openai_usage_step_context,
    summarize_openai_usage,
)
from app.search import DiscoveryQuery, SearchProvider, SourceClass


def _response(response_id: str = "resp_trace") -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        usage=SimpleNamespace(
            input_tokens=100,
            input_tokens_details=SimpleNamespace(cached_tokens=25),
            output_tokens=40,
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
        output=[SimpleNamespace(type="web_search_call"), SimpleNamespace(type="message")],
    )


def test_usage_collector_keeps_run_step_query_and_separate_cost_status() -> None:
    rows: list[dict] = []
    with openai_usage_context("discovery_trace", run_id="run-1", collector=rows):
        with openai_usage_step_context(step_id="step-1", query_id="query-1"):
            log_openai_usage(
                _response(),
                options={"model": "test-model"},
                duration_seconds=0.125,
            )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["step_id"] == "step-1"
    assert rows[0]["query_id"] == "query-1"
    assert rows[0]["response_id"] == "resp_trace"
    summary = summarize_openai_usage(rows)
    assert summary == {
        "response_count": 1,
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 40,
        "reasoning_tokens": 10,
        "web_search_calls": 1,
        "duration_ms": 125,
        "estimated_cost_usd": None,
        "estimated_cost_status": "PRICING_NOT_CONFIGURED",
        "actual_billed_cost_usd": None,
        "actual_billed_cost_status": "NOT_AVAILABLE_FROM_RESPONSE_USAGE",
    }


class _UsageSearchProvider(SearchProvider):
    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5):
        del discovery_query, limit
        log_openai_usage(
            _response("resp_query"),
            options={"model": "test-search-model"},
            duration_seconds=0.01,
        )
        return []


@pytest.mark.asyncio
async def test_tracing_search_provider_links_query_to_provider_response() -> None:
    context = DiscoveryExecutionContext.create(DiscoveryExecutionMode.DEBUG_TRACE)
    provider = _TracingSearchProvider(_UsageSearchProvider(), context)
    rows: list[dict] = []
    query = DiscoveryQuery(SourceClass.COMMUNITY, "telegram test community")

    with openai_usage_context(
        "discovery_trace",
        run_id=context.run_id,
        collector=rows,
    ):
        await provider.search(query)

    metadata = provider.call_metadata(query.query)
    assert rows[0]["response_id"] == "resp_query"
    assert rows[0]["run_id"] == context.run_id
    assert rows[0]["query_id"] == metadata["query_id"]
    assert rows[0]["step_id"] == metadata["step_id"]
