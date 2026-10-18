"""Offline verification of the per-run Telegram discovery checkpoint queue."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_discovery_stage_queue import (
    TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
    TELEGRAM_DISCOVERY_STAGES,
    TelegramDiscoveryStageQueue,
)


def test_new_queue_has_ordered_pending_checkpoints_and_survives_restart():
    store, product = MemoryRuntimeStateStore(), uuid4()
    first = TelegramDiscoveryStageQueue(
        store, product, now=datetime(2026, 10, 18, tzinfo=UTC), run_id="run-a"
    )
    first.start()
    snapshot = TelegramDiscoveryStageQueue.latest(store, product)
    assert snapshot["run_id"] == "run-a"
    assert [row["stage"] for row in snapshot["stages"]] == list(TELEGRAM_DISCOVERY_STAGES)
    assert [row["priority"] for row in snapshot["stages"]] == list(range(4))
    assert [row["status"] for row in snapshot["stages"]] == ["PENDING"] * 4
    assert snapshot["complete"] is False

    first.mark("registry", "SKIPPED_NO_CANDIDATES", reason="EMPTY_REGISTRY")
    same = TelegramDiscoveryStageQueue(
        store, product, now=datetime(2026, 10, 18, tzinfo=UTC), run_id="run-a"
    )
    assert same.snapshot()["stages"][0]["status"] == "SKIPPED_NO_CANDIDATES"
    assert same.snapshot()["complete"] is False


def test_independent_runs_keep_history_without_overwriting_a_crashed_run():
    store, product = MemoryRuntimeStateStore(), uuid4()
    first = TelegramDiscoveryStageQueue(store, product, run_id="run-a")
    first.start()
    first.mark("registry", "COMPLETED", reason="PEER_INSPECTION_COMPLETED")
    # Simulate worker dying while the native stage remains PENDING.
    second = TelegramDiscoveryStageQueue(store, product, run_id="run-b")
    second.start()
    for stage in TELEGRAM_DISCOVERY_STAGES:
        second.mark(stage, "SKIPPED_DISABLED", reason="TEST_DISABLED")

    assert first.snapshot()["stages"][1]["status"] == "PENDING"
    assert first.snapshot()["complete"] is False
    assert second.snapshot()["complete"] is True
    assert TelegramDiscoveryStageQueue.latest(store, product)["run_id"] == "run-b"
    assert len(store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE)) == 8


def test_stage_rows_contain_only_bounded_diagnostic_metadata_and_next_retry():
    store, product = MemoryRuntimeStateStore(), uuid4()
    queue = TelegramDiscoveryStageQueue(store, product)
    queue.start()
    row = queue.mark(
        "native", "DEFERRED_RATE_LIMIT",
        reason="TELEGRAM_FLOOD_WAIT", candidate_count=0,
        next_attempt_at="2026-10-21T10:00:00+00:00",
    )
    assert row["product_id"] == str(product)
    assert row["next_attempt_at"] == "2026-10-21T10:00:00+00:00"
    assert row["error_type"] is None
    assert "telegram_session" not in str(row)
    with pytest.raises(ValueError):
        queue.mark("unknown", "COMPLETED", reason="NOT_ALLOWED")
    with pytest.raises(ValueError):
        queue.mark("native", "MADE_UP_STATUS", reason="NOT_ALLOWED")
