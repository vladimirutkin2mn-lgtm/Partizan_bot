from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.analytics_schemas import (
    AnalyticsEventCreate,
    AnalyticsEventReceipt,
    ExperimentAnalyticsView,
    ExperimentMetricsView,
    ProductAnalyticsView,
    SpendCreate,
    SpendReceipt,
)
from app.execution_service import execution_service
from app.schemas import ExperimentView


@dataclass(frozen=True, slots=True)
class AttributedEvent:
    event_id: UUID
    experiment_id: UUID
    event_type: str
    actor_id: str | None
    revenue: float
    occurred_at: datetime
    properties: dict[str, Any]
    attributed_by: str


@dataclass(frozen=True, slots=True)
class SpendEntry:
    spend_id: UUID
    experiment_id: UUID
    amount: float
    occurred_at: datetime
    properties: dict[str, Any]


class InMemoryAnalyticsService:
    def __init__(self) -> None:
        self._events: dict[UUID, AttributedEvent] = {}
        self._spend: dict[UUID, SpendEntry] = {}

    def ingest_event(self, payload: AnalyticsEventCreate) -> AnalyticsEventReceipt:
        experiment, attributed_by = self._resolve_attribution(payload)
        self._ensure_measurable(experiment)

        existing = self._events.get(payload.event_id)
        if existing is not None:
            self._assert_same_event(existing, payload, experiment.id)
            return AnalyticsEventReceipt(
                event_id=existing.event_id,
                experiment_id=existing.experiment_id,
                event_type=existing.event_type,
                attributed_by=existing.attributed_by,
                duplicate=True,
            )

        event = AttributedEvent(
            event_id=payload.event_id,
            experiment_id=experiment.id,
            event_type=payload.event_type,
            actor_id=payload.actor_id,
            revenue=payload.revenue,
            occurred_at=payload.occurred_at or datetime.now(UTC),
            properties=payload.properties,
            attributed_by=attributed_by,
        )
        self._events[event.event_id] = event
        return AnalyticsEventReceipt(
            event_id=event.event_id,
            experiment_id=event.experiment_id,
            event_type=event.event_type,
            attributed_by=event.attributed_by,
        )

    def add_spend(self, experiment_id: UUID, payload: SpendCreate) -> SpendReceipt:
        experiment = execution_service.get_experiment(experiment_id)
        self._ensure_measurable(experiment)
        existing = self._spend.get(payload.spend_id)
        if existing is not None:
            if existing.experiment_id != experiment_id or existing.amount != payload.amount:
                raise ValueError("spend_id is already used for a different spend record")
            return SpendReceipt(
                spend_id=existing.spend_id,
                experiment_id=existing.experiment_id,
                amount=existing.amount,
                duplicate=True,
            )

        entry = SpendEntry(
            spend_id=payload.spend_id,
            experiment_id=experiment_id,
            amount=payload.amount,
            occurred_at=payload.occurred_at or datetime.now(UTC),
            properties=payload.properties,
        )
        self._spend[entry.spend_id] = entry
        return SpendReceipt(
            spend_id=entry.spend_id,
            experiment_id=entry.experiment_id,
            amount=entry.amount,
        )

    def experiment_analytics(self, experiment_id: UUID) -> ExperimentAnalyticsView:
        experiment = execution_service.get_experiment(experiment_id)
        events = [
            event for event in self._events.values() if event.experiment_id == experiment_id
        ]
        spend = [entry for entry in self._spend.values() if entry.experiment_id == experiment_id]
        return ExperimentAnalyticsView(
            experiment=experiment,
            event_count=len(events),
            metrics=self._metrics(events, spend),
        )

    def product_analytics(self, product_id: UUID) -> ProductAnalyticsView:
        experiments = execution_service.list_experiments(product_id)
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
        return ProductAnalyticsView(
            product_id=product_id,
            experiment_count=len(analytics),
            total_spend=total_spend,
            total_paid_users=total_paid_users,
            total_revenue=total_revenue,
            blended_cac=blended_cac,
            blended_roas=blended_roas,
            experiments=analytics,
        )

    def _resolve_attribution(
        self,
        payload: AnalyticsEventCreate,
    ) -> tuple[ExperimentView, str]:
        resolved: list[tuple[ExperimentView, str]] = []
        if payload.experiment_id is not None:
            resolved.append(
                execution_service.resolve_experiment(experiment_id=payload.experiment_id)
            )
        if payload.referral_token:
            resolved.append(
                execution_service.resolve_experiment(referral_token=payload.referral_token)
            )
        if payload.utm_content is not None:
            resolved.append(
                execution_service.resolve_experiment(growth_play_id=payload.utm_content)
            )
        if not resolved:
            raise ValueError("At least one attribution identifier is required")

        experiment_ids = {experiment.id for experiment, _ in resolved}
        if len(experiment_ids) != 1:
            raise ValueError("Attribution identifiers point to different experiments")
        methods = "+".join(method for _, method in resolved)
        return resolved[0][0], methods

    def _ensure_measurable(self, experiment: ExperimentView) -> None:
        if experiment.status not in {"RUNNING", "FINISHED"}:
            raise ValueError("Analytics events require a RUNNING or FINISHED experiment")

    def _assert_same_event(
        self,
        existing: AttributedEvent,
        payload: AnalyticsEventCreate,
        experiment_id: UUID,
    ) -> None:
        if (
            existing.experiment_id != experiment_id
            or existing.event_type != payload.event_type
            or existing.actor_id != payload.actor_id
            or existing.revenue != payload.revenue
        ):
            raise ValueError("event_id is already used for a different event")

    def _metrics(
        self,
        events: list[AttributedEvent],
        spend_entries: list[SpendEntry],
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

    def _unique_conversions(self, events: list[AttributedEvent], event_type: str) -> int:
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


analytics_service = InMemoryAnalyticsService()
