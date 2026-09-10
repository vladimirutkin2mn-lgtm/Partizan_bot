from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.analytics_schemas import ExperimentMetricsView
from app.channel_execution import PublisherMode
from app.distribution_analytics_schemas import (
    CustomerDistributionCostBreakdownView,
    CustomerDistributionEconomicsView,
    DistributionAnalyticsEventCreate,
    DistributionAnalyticsEventReceipt,
    DistributionCostBreakdownView,
    DistributionCostCategory,
    DistributionEvidenceKind,
    DistributionExperimentAnalyticsView,
    DistributionPricingAssumptionView,
    DistributionProductAnalyticsView,
    DistributionSliceMetricsView,
    DistributionSpendCreate,
    DistributionSpendReceipt,
)
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType
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
    category: DistributionCostCategory
    evidence_kind: DistributionEvidenceKind
    publisher_mode: PublisherMode | None
    action_type: DistributionActionType | None
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
        action = distribution_execution_service.get_action(experiment.action_id)
        publisher_mode = payload.publisher_mode or self._publisher_mode(action)
        action_type = payload.action_type or action.action_type

        cached = self._spend.get(payload.spend_id)
        if cached is not None:
            self._assert_same_spend(
                cached,
                payload,
                experiment_id,
                publisher_mode=publisher_mode,
                action_type=action_type,
            )
            return self._spend_receipt(cached, duplicate=True)

        entry = DistributionSpendEntry(
            spend_id=payload.spend_id,
            experiment_id=experiment_id,
            amount=payload.amount,
            category=payload.category,
            evidence_kind=payload.evidence_kind,
            publisher_mode=publisher_mode,
            action_type=action_type,
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
            self._assert_same_spend(
                existing,
                payload,
                experiment_id,
                publisher_mode=publisher_mode,
                action_type=action_type,
            )
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
        costs = self._costs(spend)
        return DistributionExperimentAnalyticsView(
            experiment=experiment,
            action=action,
            play=play,
            event_count=len(events),
            metrics=self._metrics(events, costs),
            publisher_mode=self._publisher_mode(action),
            replies=self._reply_count(events),
            removals=self._removal_count(events),
            costs=costs,
        )

    def product_analytics(self, product_id: UUID) -> DistributionProductAnalyticsView:
        experiments = distribution_execution_service.list_experiments(product_id)
        analytics = [self.experiment_analytics(item.id) for item in experiments]
        total_spend = round(sum(item.metrics.spend for item in analytics), 2)
        total_paid_users = sum(item.metrics.paid_users for item in analytics)
        total_revenue = round(sum(item.metrics.revenue for item in analytics), 2)
        total_costs = self._sum_costs(item.costs for item in analytics)
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
            total_costs=total_costs,
            experiments=analytics,
            breakdowns=self._breakdowns(analytics),
        )

    def customer_economics(self, product_id: UUID) -> CustomerDistributionEconomicsView:
        analytics = self.product_analytics(product_id)
        costs = analytics.total_costs
        return CustomerDistributionEconomicsView(
            product_id=product_id,
            experiment_count=analytics.experiment_count,
            costs=CustomerDistributionCostBreakdownView(
                research_fee=costs.research_fee,
                execution_fee=costs.execution_fee,
                distribution_spend=costs.distribution_spend,
                total=costs.customer_total,
            ),
            paid_users=analytics.total_paid_users,
            revenue=analytics.total_revenue,
            cac=analytics.blended_cac,
            roas=analytics.blended_roas,
        )

    def pricing_assumptions(
        self,
        product_id: UUID,
    ) -> list[DistributionPricingAssumptionView]:
        experiments = {
            item.id: item
            for item in distribution_execution_service.list_experiments(product_id)
        }
        self._hydrate_facts()
        groups: dict[tuple, list[DistributionSpendEntry]] = {}
        for entry in self._spend.values():
            experiment = experiments.get(entry.experiment_id)
            if experiment is None:
                continue
            if entry.category != DistributionCostCategory.OPERATING_COST:
                continue
            if entry.evidence_kind != DistributionEvidenceKind.OBSERVED:
                continue
            action = distribution_execution_service.get_action(experiment.action_id)
            mode = entry.publisher_mode or self._publisher_mode(action)
            action_type = entry.action_type or action.action_type
            groups.setdefault((action.platform, action_type, mode), []).append(entry)

        rows: list[DistributionPricingAssumptionView] = []
        for (platform, action_type, mode), entries in groups.items():
            rows.append(
                DistributionPricingAssumptionView(
                    platform=platform,
                    action_type=action_type,
                    publisher_mode=mode,
                    observed_operating_cost=round(
                        sum(item.amount for item in entries) / len(entries),
                        2,
                    ),
                    sample_count=len(entries),
                    updated_at=max(item.occurred_at for item in entries),
                )
            )
        return sorted(
            rows,
            key=lambda row: (
                row.platform.value,
                row.action_type.value,
                row.publisher_mode.value,
            ),
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
                (
                    "PUBLISHER_MODE",
                    item.publisher_mode.value,
                    item.publisher_mode.value,
                ),
                (
                    "ACTION_TYPE",
                    item.action.action_type.value,
                    item.action.action_type.value,
                ),
                (
                    "OPPORTUNITY",
                    str(item.action.opportunity_id),
                    str(item.action.opportunity_id),
                ),
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
                    replies=sum(item.replies for item in items),
                    removals=sum(item.removals for item in items),
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
            "category": entry.category.value,
            "evidence_kind": entry.evidence_kind.value,
            "publisher_mode": entry.publisher_mode.value if entry.publisher_mode else None,
            "action_type": entry.action_type.value if entry.action_type else None,
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
            category=entry.category,
            evidence_kind=entry.evidence_kind,
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
        raw_mode = payload.get("publisher_mode")
        raw_action = payload.get("action_type")
        return DistributionSpendEntry(
            spend_id=UUID(str(payload["spend_id"])),
            experiment_id=UUID(str(payload["experiment_id"])),
            amount=float(payload["amount"]),
            category=DistributionCostCategory(
                str(payload.get("category") or DistributionCostCategory.DISTRIBUTION_SPEND.value)
            ),
            evidence_kind=DistributionEvidenceKind(
                str(payload.get("evidence_kind") or DistributionEvidenceKind.OBSERVED.value)
            ),
            publisher_mode=PublisherMode(str(raw_mode)) if raw_mode else None,
            action_type=DistributionActionType(str(raw_action)) if raw_action else None,
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
        *,
        publisher_mode: PublisherMode,
        action_type: DistributionActionType,
    ) -> None:
        occurred_at_changed = (
            payload.occurred_at is not None and existing.occurred_at != payload.occurred_at
        )
        if (
            existing.experiment_id != experiment_id
            or existing.amount != payload.amount
            or existing.category != payload.category
            or existing.evidence_kind != payload.evidence_kind
            or existing.publisher_mode != publisher_mode
            or existing.action_type != action_type
            or existing.properties != payload.properties
            or occurred_at_changed
        ):
            raise ValueError("spend_id is already used for a different spend record")

    def _metrics(
        self,
        events: list[DistributionAttributedEvent],
        costs: DistributionCostBreakdownView,
    ) -> ExperimentMetricsView:
        spend = costs.customer_total
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

    def _costs(
        self,
        entries: list[DistributionSpendEntry],
    ) -> DistributionCostBreakdownView:
        totals = {
            category: round(
                sum(item.amount for item in entries if item.category == category),
                2,
            )
            for category in DistributionCostCategory
        }
        customer_total = round(
            totals[DistributionCostCategory.RESEARCH_FEE]
            + totals[DistributionCostCategory.EXECUTION_FEE]
            + totals[DistributionCostCategory.DISTRIBUTION_SPEND],
            2,
        )
        return DistributionCostBreakdownView(
            research_fee=totals[DistributionCostCategory.RESEARCH_FEE],
            execution_fee=totals[DistributionCostCategory.EXECUTION_FEE],
            distribution_spend=totals[DistributionCostCategory.DISTRIBUTION_SPEND],
            operating_cost=totals[DistributionCostCategory.OPERATING_COST],
            customer_total=customer_total,
        )

    def _sum_costs(self, rows) -> DistributionCostBreakdownView:
        research = round(sum(item.research_fee for item in rows), 2)
        execution = round(sum(item.execution_fee for item in rows), 2)
        distribution = round(sum(item.distribution_spend for item in rows), 2)
        operating = round(sum(item.operating_cost for item in rows), 2)
        return DistributionCostBreakdownView(
            research_fee=research,
            execution_fee=execution,
            distribution_spend=distribution,
            operating_cost=operating,
            customer_total=round(research + execution + distribution, 2),
        )

    def _publisher_mode(self, action) -> PublisherMode:
        raw = action.operational_metadata.get("publisher_mode")
        if raw:
            try:
                return PublisherMode(str(raw).upper())
            except ValueError:
                pass
        observations = action.operational_metadata.get("external_observations")
        if isinstance(observations, dict) and "partizan_managed" in observations:
            return PublisherMode.PARTIZAN_MANAGED
        return PublisherMode.MANUAL

    def _reply_count(self, events: list[DistributionAttributedEvent]) -> int:
        replies = [event for event in events if event.event_type == "REPLY"]
        explicit_counts = [
            int(event.properties["count"])
            for event in replies
            if isinstance(event.properties.get("count"), int)
            and not isinstance(event.properties.get("count"), bool)
        ]
        if explicit_counts:
            return max(explicit_counts)
        return len(replies)

    def _removal_count(self, events: list[DistributionAttributedEvent]) -> int:
        return 1 if any(event.event_type == "REMOVED" for event in events) else 0

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
