"""No-network paid Telegram discovery budget and usage-ledger tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_paid_discovery_budget import (
    TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE,
    TELEGRAM_PAID_DISCOVERY_DENIAL_NAMESPACE,
    TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE,
    _PaidTelegramDiscoveryBudgetProvider,
)


class FakePaid:
    def __init__(self, fail=False):
        self.calls: list[str] = []
        self.fail = fail

    async def search(self, discovery_query, limit=5):
        await asyncio.sleep(0)
        self.calls.append(discovery_query.query)
        if self.fail:
            raise RuntimeError("unknown provider billing")
        return []


def _q(query="site:t.me FemDom groups"):
    return SimpleNamespace(query=query)


def _provider(store, delegate, project, at, monthly="0.35", envelope="0.20"):
    return _PaidTelegramDiscoveryBudgetProvider(
        delegate, store, project,
        monthly_budget_usd=monthly,
        query_reservation_usd=envelope,
        now=lambda: at,
    )


@pytest.mark.asyncio
async def test_missing_price_envelope_blocks_all_calls_before_daily_claim():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(), uuid4()
    for monthly, envelope in [(None, None), ("1", None), (None, "0.2"), ("0", "0.1")]:
        provider = _provider(
            store, delegate, product, datetime(2026, 10, 18, tzinfo=UTC),
            monthly, envelope,
        )
        assert await provider.search(_q()) == []
        assert provider.last_paid_status == "COST_ENVELOPE_NOT_CONFIGURED"
    assert delegate.calls == []
    assert store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE) == []
    denial = store.list_namespace(TELEGRAM_PAID_DISCOVERY_DENIAL_NAMESPACE)
    assert len(denial) == 1
    assert denial[0]["actual_billed_cost_usd"] is None


@pytest.mark.asyncio
async def test_one_atomic_daily_claim_survives_parallel_queries_and_restart():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(), uuid4()
    at = datetime(2026, 10, 18, 9, tzinfo=UTC)
    a = _provider(store, delegate, product, at, monthly="2", envelope="0.25")
    b = _provider(store, delegate, product, at, monthly="2", envelope="0.25")
    assert await asyncio.gather(a.search(_q("site:t.me FemDom")), b.search(_q())) == [[], []]
    assert len(delegate.calls) == 1
    assert await _provider(store, delegate, product, at).search(_q()) == []
    assert len(delegate.calls) == 1
    rows = store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE)
    assert rows[0]["maximum_paid_queries"] == 1
    assert rows[0]["status"] == "COMPLETED_BILLING_UNKNOWN"
    assert rows[0]["estimated_cost_reservation_usd"] == "0.25"
    assert rows[0]["usage_status"] == "NOT_REPORTED"
    assert rows[0]["actual_billed_cost_usd"] is None
    assert len(store.list_namespace(TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_monthly_envelope_stops_next_day_without_paid_provider_call():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(), uuid4()
    at = datetime(2026, 10, 18, tzinfo=UTC)
    first = _provider(store, delegate, product, at)
    second = _provider(store, delegate, product, at + timedelta(days=1))
    assert await first.search(_q()) == []
    assert await second.search(_q()) == []
    assert second.last_paid_status == "MONTHLY_BUDGET_EXHAUSTED"
    assert len(delegate.calls) == 1
    claims = store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE)
    assert {row["status"] for row in claims} == {
        "COMPLETED_BILLING_UNKNOWN", "MONTHLY_BUDGET_EXHAUSTED"
    }
    assert len(store.list_namespace(TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE)) == 1
    assert store.list_namespace(TELEGRAM_PAID_DISCOVERY_DENIAL_NAMESPACE)[0][
        "status"
    ] == "MONTHLY_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_provider_error_keeps_reservation_and_unknown_actual_charge():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(fail=True), uuid4()
    at = datetime(2026, 10, 18, tzinfo=UTC)
    provider = _provider(store, delegate, product, at)
    with pytest.raises(RuntimeError, match="unknown provider billing"):
        await provider.search(_q())
    assert (await _provider(store, delegate, product, at).search(_q())) == []
    assert len(delegate.calls) == 1
    row = store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE)[0]
    assert row["status"] == "PROVIDER_FAILED_CHARGE_UNKNOWN"
    assert row["actual_billed_cost_usd"] is None


@pytest.mark.asyncio
async def test_other_platform_queries_do_not_consume_telegram_budget():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(), uuid4()
    at = datetime(2026, 10, 18, tzinfo=UTC)
    provider = _provider(store, delegate, product, at, None, None)
    assert await provider.search(_q("site:reddit.com founders")) == []
    assert len(delegate.calls) == 1
    assert store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE) == []


@pytest.mark.asyncio
async def test_other_products_have_independent_monthly_envelope():
    store, delegate = MemoryRuntimeStateStore(), FakePaid()
    at = datetime(2026, 10, 18, tzinfo=UTC)
    for product in [uuid4(), uuid4()]:
        assert await _provider(store, delegate, product, at).search(_q()) == []
    assert len(delegate.calls) == 2
    assert len(store.list_namespace(TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE)) == 2


@pytest.mark.asyncio
async def test_monthly_slots_are_atomic_across_days_and_reset_next_month():
    store, delegate, product = MemoryRuntimeStateStore(), FakePaid(), uuid4()
    a = datetime(2026, 10, 18, tzinfo=UTC)
    b = datetime(2026, 10, 19, tzinfo=UTC)
    results = await asyncio.gather(
        _provider(store, delegate, product, a).search(_q("site:t.me first")),
        _provider(store, delegate, product, b).search(_q("site:t.me second")),
    )
    assert results == [[], []]
    assert len(delegate.calls) == 1
    assert await _provider(
        store, delegate, product, datetime(2026, 11, 1, tzinfo=UTC)
    ).search(_q()) == []
    assert len(delegate.calls) == 2
    assert len(store.list_namespace(TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE)) == 2
