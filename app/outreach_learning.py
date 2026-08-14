from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, Field

from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_types import DistributionActionType
from app.runtime_store import RuntimeStateStore, get_runtime_store

OUTREACH_LEARNING_FEED_NAMESPACE = "outreach_learning_feed_state"


class OutreachLearningFeedItemView(BaseModel):
    experiment_id: UUID
    growth_decision_id: UUID
    growth_action: str
    duplicate: bool = False


class OutreachLearningFeedView(BaseModel):
    product_id: UUID
    evaluated: list[OutreachLearningFeedItemView] = Field(default_factory=list)


class OutreachLearningFeedService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def feed(self, product_id: UUID) -> OutreachLearningFeedView:
        evaluated: list[OutreachLearningFeedItemView] = []
        for experiment in distribution_execution_service.list_experiments(product_id):
            if experiment.status not in {
                DistributionExperimentStatus.RUNNING,
                DistributionExperimentStatus.FINISHED,
            }:
                continue
            action = distribution_execution_service.get_action(experiment.action_id)
            if action.action_type != DistributionActionType.OUTREACH_EMAIL:
                continue

            analytics = distribution_analytics_service.experiment_analytics(experiment.id)
            if analytics.event_count <= 0:
                continue
            fingerprint = self._fingerprint(
                analytics.event_count,
                analytics.metrics.model_dump(mode="json"),
            )
            state = self._store.get(OUTREACH_LEARNING_FEED_NAMESPACE, str(experiment.id))
            if state is not None and state.get("fingerprint") == fingerprint:
                continue

            decision = distribution_growth_manager_service.evaluate(experiment.id)
            self._store.put(
                OUTREACH_LEARNING_FEED_NAMESPACE,
                str(experiment.id),
                {
                    "product_id": str(product_id),
                    "experiment_id": str(experiment.id),
                    "fingerprint": fingerprint,
                    "growth_decision_id": str(decision.id),
                },
            )
            evaluated.append(
                OutreachLearningFeedItemView(
                    experiment_id=experiment.id,
                    growth_decision_id=decision.id,
                    growth_action=decision.action,
                    duplicate=decision.duplicate,
                )
            )
        return OutreachLearningFeedView(product_id=product_id, evaluated=evaluated)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_LEARNING_FEED_NAMESPACE)

    def _fingerprint(self, event_count: int, metrics: dict) -> str:
        payload = json.dumps(
            {"event_count": event_count, "metrics": metrics},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


outreach_learning_feed_service = OutreachLearningFeedService()
