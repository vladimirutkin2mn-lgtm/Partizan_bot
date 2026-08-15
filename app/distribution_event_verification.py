from uuid import UUID

from app.distribution_analytics_schemas import (
    DistributionAnalyticsEventCreate,
    DistributionAnalyticsEventVerification,
)
from app.distribution_analytics_service import DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.runtime_store import RuntimeStateStore, get_runtime_store


class DistributionEventProductMismatchError(ValueError):
    """The supplied Product Event Key cannot validate another product's experiment."""


class DistributionEventVerificationService:
    """Validate the real ingestion contract without recording analytics facts."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def verify(
        self,
        product_id: UUID,
        payload: DistributionAnalyticsEventCreate,
    ) -> DistributionAnalyticsEventVerification:
        experiment, attributed_by = distribution_execution_service.resolve_experiment(
            experiment_id=payload.experiment_id,
            referral_token=payload.referral_token,
            action_id=payload.action_id,
        )
        if experiment.product_id != product_id:
            raise DistributionEventProductMismatchError(
                "Distribution event key cannot validate another product"
            )
        if experiment.status not in {
            DistributionExperimentStatus.RUNNING,
            DistributionExperimentStatus.FINISHED,
        }:
            raise ValueError(
                "Distribution analytics require a RUNNING or FINISHED experiment"
            )

        existing = self._store.get(
            DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
            str(payload.event_id),
        )
        duplicate = existing is not None
        if existing is not None:
            self._assert_same_event(existing, payload, experiment.id)

        return DistributionAnalyticsEventVerification(
            event_id=payload.event_id,
            experiment_id=experiment.id,
            event_type=payload.event_type,
            attributed_by=attributed_by,
            duplicate=duplicate,
        )

    def _assert_same_event(
        self,
        existing: dict,
        payload: DistributionAnalyticsEventCreate,
        experiment_id: UUID,
    ) -> None:
        if (
            UUID(str(existing["experiment_id"])) != experiment_id
            or str(existing["event_type"]) != payload.event_type
            or existing.get("actor_id") != payload.actor_id
            or float(existing.get("revenue", 0)) != payload.revenue
        ):
            raise ValueError("event_id is already used for a different distribution event")


distribution_event_verification_service = DistributionEventVerificationService()
