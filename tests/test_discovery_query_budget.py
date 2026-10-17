from __future__ import annotations

import pytest

from app.discovery_execution_context import (
    DiscoveryBudgetExhausted,
    DiscoveryExecutionContext,
    DiscoveryExecutionMode,
)
from app.manual_discovery_trace import _TracingSearchProvider, build_parser
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass


class _CountingSearchProvider(SearchProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        self.calls += 1
        return [
            SearchHit(
                title="Test",
                url="https://t.me/test_community",
                snippet="test",
                query=discovery_query.query,
                source_class=discovery_query.source_class,
            )
        ][:limit]


def test_context_reserves_before_provider_call_and_reports_remaining_budget() -> None:
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.DEBUG_TRACE,
        max_logical_queries=1,
    )

    query_id = context.reserve_query_id()

    assert query_id.endswith(":query:1")
    assert context.as_dict()["logical_query_budget"] == {
        "max": 1,
        "reserved": 1,
        "remaining": 0,
        "exhausted": True,
    }
    with pytest.raises(DiscoveryBudgetExhausted):
        context.reserve_query_id()


@pytest.mark.asyncio
async def test_tracing_provider_never_calls_delegate_after_budget_is_exhausted() -> None:
    delegate = _CountingSearchProvider()
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.DEBUG_TRACE,
        max_logical_queries=1,
    )
    provider = _TracingSearchProvider(delegate, context)
    first = DiscoveryQuery(SourceClass.COMMUNITY, "first")
    second = DiscoveryQuery(SourceClass.COMMUNITY, "second")

    await provider.search(first)
    with pytest.raises(DiscoveryBudgetExhausted):
        await provider.search(second)

    assert delegate.calls == 1
    assert context.as_dict()["logical_query_budget"]["reserved"] == 1


def test_manual_trace_cli_defaults_to_one_paid_logical_query() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--product-id", "52d7e59e-5ac5-4363-b829-c93e33e48272"]
    )

    assert args.max_query_count == 1
