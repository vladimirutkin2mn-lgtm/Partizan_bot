from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.analytics_schemas import ExperimentMetricsView
from app.distribution_analytics_schemas import (
    DistributionAnalyticsEventCreate,
    DistributionAnalyticsEventReceipt,
    DistributionExperimentAnalyticsView,
    DistributionProductAnalyticsView,
    DistributionSliceMetricsView,
    DistributionSpendCreate,
    DistributionSpendReceipt,
)
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE = "distribution_analytics_event"
DISTRIBUTION_SPEND_NAMESPACE = "distribution_experiment_spend"


@dataclass(frozen=True, slots=True)
class DistributionAttributedEvent:
    event_id: UUID
    experiment_id: UUID
    event_type: str
    actor_id: str | None
    revenue: float
    occurred_at: datetime
    properties: dict[str, Any]
    attributed_by: str


@dataclass(frozen=True, slots=True)
class DistributionSpendEntry:
    spend_id: UUID
    experiment_id: UUID
    amount: float
    occurred_at: datetime
    properties: dict[str, Any]


class InMemoryDistributionAnalyticsService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._events: dict[UUID, DistributionAttributedEvent] = {}
        self._spend: dict[UUID, DistributionSpendEntry] = {}

    def ingest_event(
        self,
        payload: DistributionAnalyticsEventCreate,
    ) -> DistributionAnalyticsEventReceipt:
        experiment, attributed_by = distribution_execution_service.resolve_experiment(
            experiment_id=payload.experiment_id,
            referral_token=payload.referral_token,
            action_id=payload.action_id,
        )
        self._ensure_measurable(experiment.status)

        cached = self._events.get(payload.event_id)
        if cached is not None:
            self._assert_same_event(cached, payload, experiment.id)
            return self._event_receipt(cached, duplicate=True)

        event = DistributionAttributedEvent(
            event_id=payload.event_id,
            experiment_id=experiment.id,
            event_type=payload.event_type,
            actor_id=payload.actor_id,
            revenue=payload.revenue,
            occurred_at=payload.occurred_at or datetime.now(UTC),
            properties=payload.properties,
            attributed_by=attributed_by,
        )
        inserted = self._store.put_if_absent(
            DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
            str(event.event_id),
            self._event_payload(event),
        )
        if not inserted:
            existing = self._get_event(event.event_id)
            if existing is None:
                raise RuntimeError("event idempotency reservation disappeared after conflict")
            self._assert_same_event(existing, payload, experiment.id)
            return self._event_receipt(existing, duplicate=True)

        self._events[event.event_id] = event
        return self._event_receipt(event)

    def add_spend(
        self,
        experiment_id: UUID,
        payload: DistributionSpendCreate,
    ) -> DistributionSpendReceipt:
        experiment = distribution_execution_service.get_experiment(experiment_id)
        self._ensure_measurable(experiment.status)

        cached = self._spend.get(payload.spend_id)
        if cached is not None:
            self._assert_same_spend(cached, payload, experiment_id)
            return self._spend_receipt(cached, duplicate=True)

        entry = DistributionSpendEntry(
            spend_id=payload.spend_id,
            experiment_id=experiment_id,
            amount=payload.amount,
            occurred_at=payload.occurred_at or datetime.now(UTC),
            properties=payload.properties,
        )
        inserted = self._store.put_if_absent(
            DISTRIBUTION_SPEND_NAMESPACE,
            str(entry.spend_id),
            self._spend_payload(entry),
        )
        if not inserted:
            existing = self._get_spend(entry.spend_id)
            if existing is None:
                raise RuntimeError("spend idempotency reservation disappeared after conflict")
            self._assert_same_spend(existing, payload, experiment_id)
            return self._spend_receipt(existing, duplicate=True)

        self._spend[entry.spend_id] = entry
        return self._spend_receipt(entry)

    def experiment_analytics(
        self,
        experiment_id: UUID,
    ) -> DistributionExperimentAnalyticsView:
        experiment = distribution_execution_service.get_experiment(experiment_id)
        action = distribution_execution_service.get_action(experiment.action_id)
        play = distribution_play_service.find(
            experiment.product_id,
            experiment.distribution_play_id,
        )
        self._hydrate_facts()
        events = [
            event for event in self._events.values() if event.experiment_id == experiment_id
        ]
        spend = [entry for entry in self._spend.values() if entry.experiment_id == experiment_id]
        return DistributionExperimentAnalyticsView(
            experiment=experiment,
            action=action,
            play=play,
            event_count=len(events),
            metrics=self._metrics(events, spend),
        )

    def product_analytics(self, product_id: UUID) -> DistributionProductAnalyticsView:
        experiments = distribution_execution_service.list_experiments(product_id)
        analytics = [self.experiment_analytics(item.id) for item in experiments]
        total_spend = round(sum(item.metrics.spend for item in analytics), 2)
        total_paid_users = sum(item.metrics.paid_users for item in analytics)
        total_revenue = round(sum(item.metrics.revenue for item in analytics), 2)
        blended_cac = (
            round(total_spend / total_paid_users, 2) if total_paid_users else None
        )
        blended_roas = round(total_revenue / total_spend, 3) if total_spend else None
        analytics.sort(
            key=lambda item: (
                item.metrics.cac is None,
                item.metrics.cac if item.metrics.cac is not None else float("inf"),
                str(item.experiment.id),
            )
        )
        return DistributionProductAnalyticsView(
            product_id=product_id,
            experiment_count=len(analytics),
            total_spend=total_spend,
            total_paid_users=total_paid_users,
            total_revenue=total_revenue,
            blended_cac=blended_cac,
            blended_roas=blended_roas,
            experiments=analytics,
            breakdowns=self._breakdowns(analytics),
        )

    def _breakdowns(
        self,
        analytics: list[DistributionExperimentAnalyticsView],
    ) -> list[DistributionSliceMetricsView]:
        groups: dict[tuple[str, str, str], list[DistributionExperimentAnalyticsView]] = {}
        for item in analytics:
            keys = [
                ("PLATFORM", item.play.platform.value, item.play.platform.value),
                ("TACTIC", item.play.tactic_id, item.play.tactic_id),
            ]
            if item.action.distribution_identity_id is not None:
                identity_key = str(item.action.distribution_identity_id)
                keys.append(("IDENTITY", identity_key, identity_key))
            for key in keys:
                groups.setdefault(key, []).append(item)

        rows: list[DistributionSliceMetricsView] = []
        for (dimension, key, label), items in groups.items():
            spend = round(sum(item.metrics.spend for item in items), 2)
            paid = sum(item.metrics.paid_users for item in items)
            revenue = round(sum(item.metrics.revenue for item in items), 2)
            rows.append(
                DistributionSliceMetricsView(
                    dimension=dimension,
                    key=key,
                    label=label,
                    experiment_count=len(items),
                    spend=spend,
                    paid_users=paid,
                    revenue=revenue,
                    cac=round(spend / paid, 2) if paid else None,
                    roas=round(revenue / spend, 3) if spend else None,
                )
            )
        return sorted(rows, key=lambda row: (row.dimension, row.key))

    def _get_event(self, event_id: UUID) -> DistributionAttributedEvent | None:
        cached = self._events.get(event_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE, str(event_id))
        if payload is None:
            return None
        event = self._event_from_payload(payload)
        self._events[event_id] = event
        return event

    def _get_spend(self, spend_id: UUID) -> DistributionSpendEntry | None:
        cached = self._spend.get(spend_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_SPEND_NAMESPACE, str(spend_id))
        if payload is None:
            return None
        entry = self._spend_from_payload(payload)
        self._spend[spend_id] = entry
        return entry

    def _hydrate_facts(self) -> None:
        for payload in self._store.list_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE):
            event = self._event_from_payload(payload)
            self._events[event.event_id] = event
        for payload in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE):
            entry = self._spend_from_payload(payload)
            self._spend[entry.spend_id] = entry

    def _event_payload(self, event: DistributionAttributedEvent) -> dict[str, Any]:
        return {
            "event_id": str(event.event_id),
            "experiment_id": str(event.experiment_id),
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "revenue": event.revenue,
            "occurred_at": event.occurred_at.isoformat(),
            "properties": event.properties,
            "attributed_by": event.attributed_by,
        }

    def _spend_payload(self, entry: DistributionSpendEntry) -> dict[str, Any]:
        return {
            "spend_id": str(entry.spend_id),
            "experiment_id": str(entry.experiment_id),
            "amount": entry.amount,
            "occurred_at": entry.occurred_at.isoformat(),
            "properties": entry.properties,
        }

    def _event_receipt(
        self,
        event: DistributionAttributedEvent,
        *,
        duplicate: bool = False,
    ) -> DistributionAnalyticsEventReceipt:
        return DistributionAnalyticsEventReceipt(
            event_id=event.event_id,
            experiment_id=event.experiment_id,
            event_type=event.event_type,
            attributed_by=event.attributed_by,
            duplicate=duplicate,
        )

    def _spend_receipt(
        self,
        entry: DistributionSpendEntry,
        *,
        duplicate: bool = False,
    ) -> DistributionSpendReceipt:
        return DistributionSpendReceipt(
            spend_id=entry.spend_id,
            experiment_id=entry.experiment_id,
            amount=entry.amount,
            duplicate=duplicate,
        )

    def _event_from_payload(self, payload: dict) -> DistributionAttributedEvent:
        return DistributionAttributedEvent(
            event_id=UUID(str(payload["event_id"])),
            experiment_id=UUID(str(payload["experiment_id"])),
            event_type=str(payload["event_type"]),
            actor_id=payload.get("actor_id"),
            revenue=float(payload.get("revenue", 0)),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
            properties=dict(payload.get("properties", {})),
            attributed_by=str(payload["attributed_by"]),
        )

    def _spend_from_payload(self, payload: dict) -> DistributionSpendEntry:
        return DistributionSpendEntry(
            spend_id=UUID(str(payload["spend_id"])),
            experiment_id=UUID(str(payload["experiment_id"])),
            amount=float(payload["amount"]),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
            properties=dict(payload.get("properties", {})),
        )

    def _ensure_measurable(self, status: DistributionExperimentStatus) -> None:
        if status not in {
            DistributionExperimentStatus.RUNNING,
            DistributionExperimentStatus.FINISHED,
        }:
            raise ValueError(
                "Distribution analytics require a RUNNING or FINISHED experiment"
            )

    def _assert_same_event(
        self,
        existing: DistributionAttributedEvent,
        payload: DistributionAnalyticsEventCreate,
        experiment_id: UUID,
    ) -> None:
        occurred_at_changed = (
            payload.occurred_at is not None and existing.occurred_at != payload.occurred_at
        )
        if (
            existing.experiment_id != experiment_id
            or existing.event_type != payload.event_type
            or existing.actor_id != payload.actor_id
            or existing.revenue != payload.revenue
            or existing.properties != payload.properties
            or occurred_at_changed
        ):
            raise ValueError("event_id is already used for a different distribution event")

    def _assert_same_spend(
        self,
        existing: DistributionSpendEntry,
        payload: DistributionSpendCreate,
        experiment_id: UUID,
    ) -> None:
        occurred_at_changed = (
            payload.occurred_at is not None and existing.occurred_at != payload.occurred_at
        )
        if (
            existing.experiment_id != experiment_id
            or existing.amount != payload.amount
            or existing.properties != payload.properties
            or occurred_at_changed
        ):
            raise ValueError("spend_id is already used for a different spend record")

    def _metrics(
        self,
        events: list[DistributionAttributedEvent],
        spend_entries: list[DistributionSpendEntry],
    ) -> ExperimentMetricsView:
        spend = round(sum(entry.amount for entry in spend_entries), 2)
        visits = sum(event.event_type == "VISIT" for event in events)
        signups = self._unique_conversions(events, "SIGNUP")
        activated = self._unique_conversions(events, "ACTIVATED")
        paid_users = self._unique_conversions(events, "PAID")
        transactions = sum(event.event_type == "PAID" for event in events)
        revenue = round(
            sum(event.revenue for event in events if event.event_type == "PAID"),
            2,
        )
        return ExperimentMetricsView(
            spend=spend,
            visits=visits,
            signups=signups,
            activated_users=activated,
            paid_users=paid_users,
            transactions=transactions,
            revenue=revenue,
            visit_to_signup_rate=self._ratio(signups, visits),
            signup_to_paid_rate=self._ratio(paid_users, signups),
            cac=round(spend / paid_users, 2) if paid_users else None,
            roas=round(revenue / spend, 3) if spend else None,
            revenue_per_paid_user=(
                round(revenue / paid_users, 2) if paid_users else None
            ),
        )

    def _unique_conversions(
        self,
        events: list[DistributionAttributedEvent],
        event_type: str,
    ) -> int:
        identities = {
            f"actor:{event.actor_id}" if event.actor_id else f"event:{event.event_id}"
            for event in events
            if event.event_type == event_type
        }
        return len(identities)

    def _ratio(self, numerator: int, denominator: int) -> float | None:
        if denominator == 0:
            return None
        return round(numerator / denominator, 4)

    def reset(self) -> None:
        self._events.clear()
        self._spend.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE)
            self._store.clear_namespace(DISTRIBUTION_SPEND_NAMESPACE)


distribution_analytics_service = InMemoryDistributionAnalyticsService()
