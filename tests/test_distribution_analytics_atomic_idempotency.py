from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_analytics_schemas import (
    DistributionAnalyticsEventCreate,
    DistributionSpendCreate,
)
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
    InMemoryDistributionAnalyticsService,
)
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.runtime_store import MemoryRuntimeStateStore


class AtomicOnlyStore(MemoryRuntimeStateStore):
    def put(self, namespace: str, key: str, payload: dict) -> None:
        if namespace in {
            DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
            DISTRIBUTION_SPEND_NAMESPACE,
        }:
            raise AssertionError("immutable analytics facts must use put_if_absent")
        super().put(namespace, key, payload)


def test_event_ingestion_uses_atomic_reservation_and_accepts_same_retry(monkeypatch) -> None:
    store = AtomicOnlyStore()
    service = InMemoryDistributionAnalyticsService(store=store)
    experiment_id = uuid4()
    event_id = uuid4()
    experiment = SimpleNamespace(id=experiment_id, status=DistributionExperimentStatus.RUNNING)
    monkeypatch.setattr(
        distribution_execution_service,
        "resolve_experiment",
        lambda **_: (experiment, "experiment_id"),
    )
    payload = DistributionAnalyticsEventCreate(
        event_id=event_id,
        event_type="PAID",
        experiment_id=experiment_id,
        actor_id="user-1",
        revenue=19.0,
        properties={"plan": "pro"},
    )

    first = service.ingest_event(payload)
    recreated = InMemoryDistributionAnalyticsService(store=store)
    duplicate = recreated.ingest_event(payload)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert store.get(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE, str(event_id)) is not None


def test_conflicting_event_retry_is_rejected_after_atomic_conflict(monkeypatch) -> None:
    store = AtomicOnlyStore()
    service = InMemoryDistributionAnalyticsService(store=store)
    experiment_id = uuid4()
    event_id = uuid4()
    experiment = SimpleNamespace(id=experiment_id, status=DistributionExperimentStatus.RUNNING)
    monkeypatch.setattr(
        distribution_execution_service,
        "resolve_experiment",
        lambda **_: (experiment, "experiment_id"),
    )
    original = DistributionAnalyticsEventCreate(
        event_id=event_id,
        event_type="SIGNUP",
        experiment_id=experiment_id,
        actor_id="user-1",
        properties={"source": "landing-a"},
    )
    service.ingest_event(original)

    conflicting = DistributionAnalyticsEventCreate(
        event_id=event_id,
        event_type="SIGNUP",
        experiment_id=experiment_id,
        actor_id="user-1",
        properties={"source": "landing-b"},
    )
    recreated = InMemoryDistributionAnalyticsService(store=store)

    with pytest.raises(ValueError, match="event_id is already used"):
        recreated.ingest_event(conflicting)


def test_spend_ingestion_uses_atomic_reservation_and_checks_full_retry(monkeypatch) -> None:
    store = AtomicOnlyStore()
    service = InMemoryDistributionAnalyticsService(store=store)
    experiment_id = uuid4()
    spend_id = uuid4()
    experiment = SimpleNamespace(id=experiment_id, status=DistributionExperimentStatus.RUNNING)
    monkeypatch.setattr(distribution_execution_service, "get_experiment", lambda _: experiment)
    occurred_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    payload = DistributionSpendCreate(
        spend_id=spend_id,
        amount=12.5,
        occurred_at=occurred_at,
        properties={"provider": "test"},
    )

    first = service.add_spend(experiment_id, payload)
    recreated = InMemoryDistributionAnalyticsService(store=store)
    duplicate = recreated.add_spend(experiment_id, payload)

    assert first.duplicate is False
    assert duplicate.duplicate is True

    changed = payload.model_copy(update={"properties": {"provider": "other"}})
    with pytest.raises(ValueError, match="spend_id is already used"):
        InMemoryDistributionAnalyticsService(store=store).add_spend(
            experiment_id,
            changed,
        )
