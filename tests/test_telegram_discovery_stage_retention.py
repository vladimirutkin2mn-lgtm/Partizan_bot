"""Retention never touches canonical communities, billing or FloodWait state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_discovery_stage_queue import (
    TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE,
    TELEGRAM_DISCOVERY_MAX_HISTORY_DAYS,
    TELEGRAM_DISCOVERY_MAX_HISTORY_RUNS,
    TELEGRAM_DISCOVERY_RETENTION_NAMESPACE,
    TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
    TelegramDiscoveryStageQueue,
)


def _stage_row(store, product_id, run_id, started_at, stage="native"):
    row_id = f"{product_id}:{run_id}:{stage}"
    store.put(
        TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
        row_id,
        {
            "id": row_id, "product_id": str(product_id),
            "run_id": run_id, "stage": stage,
            "started_at": started_at.isoformat(),
            "status": "COMPLETED",
        },
    )


def test_automatic_daily_retention_removes_only_expired_discovery_rows():
    store, product_id, other_id = MemoryRuntimeStateStore(), uuid4(), uuid4()
    now = datetime(2026, 10, 18, 15, tzinfo=UTC)
    _stage_row(store, product_id, "expired-run", now - timedelta(days=31))
    _stage_row(store, product_id, "recent-run", now - timedelta(days=3))
    _stage_row(store, other_id, "other-old", now - timedelta(days=400))
    store.put("telegram_paid_discovery_month_slot", "protected", {"id": "protected"})
    store.put("telegram_community_registry", "community", {"id": "community"})
    store.put("telegram_research_flood_wait", "wait", {"id": "wait"})

    queue = TelegramDiscoveryStageQueue(store, product_id, now=now, run_id="latest")
    queue.start()
    rows = store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE)
    assert not any(item["run_id"] == "expired-run" for item in rows)
    assert any(item["run_id"] == "recent-run" for item in rows)
    assert any(item["run_id"] == "other-old" for item in rows)
    assert len([item for item in rows if item["run_id"] == "latest"]) == 4
    assert store.get(TELEGRAM_DISCOVERY_RETENTION_NAMESPACE, str(product_id))[
        "removed_stage_rows"
    ] == 1
    assert store.list_namespace("telegram_paid_discovery_month_slot")
    assert store.list_namespace("telegram_community_registry")
    assert store.list_namespace("telegram_research_flood_wait")
    assert store.get(TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE, str(product_id))[
        "run_id"
    ] == "latest"


def test_checkpoint_retention_caps_history_even_when_all_runs_are_recent():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    now = datetime(2026, 10, 18, 15, tzinfo=UTC)
    for n in range(80):
        _stage_row(
            store, product_id, f"run-{n:03d}",
            now - timedelta(minutes=80 - n),
        )
    queue = TelegramDiscoveryStageQueue(store, product_id, now=now, run_id="latest")
    queue.start()
    runs = {
        row["run_id"]
        for row in store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE)
        if row["product_id"] == str(product_id)
    }
    assert len(runs) == TELEGRAM_DISCOVERY_MAX_HISTORY_RUNS
    assert "latest" in runs
    assert "run-000" not in runs
    assert "run-079" in runs


def test_retention_never_drops_latest_run_even_if_its_clock_is_old():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    now = datetime(2026, 10, 18, 15, tzinfo=UTC)
    latest = TelegramDiscoveryStageQueue(
        store, product_id, now=now - timedelta(days=50), run_id="latest"
    )
    latest.start()
    _stage_row(store, product_id, "recent", now - timedelta(days=2))
    removed = latest.prune(now=now)
    assert removed == 0
    assert len([
        row for row in store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE)
        if row["run_id"] == "latest"
    ]) == 4
    assert TELEGRAM_DISCOVERY_MAX_HISTORY_DAYS == 30


def test_retention_marker_is_one_per_project_not_one_per_day():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    now = datetime(2026, 10, 18, 15, tzinfo=UTC)
    for n in range(4):
        TelegramDiscoveryStageQueue(
            store, product_id, now=now + timedelta(minutes=n),
            run_id=f"same-day-{n}",
        ).start()
    assert len(store.list_namespace(TELEGRAM_DISCOVERY_RETENTION_NAMESPACE)) == 1
    assert len(store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE)) == 16
