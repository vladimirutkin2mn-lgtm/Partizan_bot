from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.distribution_analytics_schemas import DistributionAnalyticsEventCreate
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_schemas import DistributionExperimentStatus, DistributionExperimentView
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

SELF_DOGFOOD_ATTRIBUTION_COOKIE = "ptz_self_ref"
SELF_DOGFOOD_ATTRIBUTION_NAMESPACE = "self_dogfood_project_attribution"
_SELF_DOGFOOD_EVENT_NAMESPACE = UUID("e63f34c3-7f29-5ca2-86c8-4cbd2d994af0")
_logger = logging.getLogger(__name__)


class SelfDogfoodProjectBinding(BaseModel):
    project_id: UUID
    product_id: UUID
    experiment_id: UUID
    referral_token: str
    created_at: datetime


class SelfDogfoodSnapshot(BaseModel):
    configured_product_id: UUID | None = None
    product_exists: bool = False
    experiment_count: int = Field(default=0, ge=0)
    measurable_experiments: int = Field(default=0, ge=0)
    running_experiments: int = Field(default=0, ge=0)
    visits: int = Field(default=0, ge=0)
    signups: int = Field(default=0, ge=0)
    activated_users: int = Field(default=0, ge=0)
    paid_users: int = Field(default=0, ge=0)
    spend_usd: float = Field(default=0, ge=0)
    revenue_usd: float = Field(default=0, ge=0)
    cac_usd: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
    learning_entries: int = Field(default=0, ge=0)
    latest_decision: str | None = None
    blockers: list[str] = Field(default_factory=list)
    proof_ready: bool = False


class SelfDogfoodService:
    """First-party attribution bridge when Partizan acquires customers for Partizan itself."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def should_capture_referral(
        self,
        experiment: DistributionExperimentView,
        destination: str,
        settings: Settings,
    ) -> bool:
        configured = settings.partizan_self_dogfood_product_id
        public_base = settings.partizan_public_base_url
        if configured is None or public_base is None or experiment.product_id != configured:
            return False
        if experiment.status not in {
            DistributionExperimentStatus.RUNNING,
            DistributionExperimentStatus.FINISHED,
        }:
            return False
        return self._origin(destination) == self._origin(public_base)

    def bind_project(
        self,
        project_id: UUID,
        referral_token: str | None,
        settings: Settings,
    ) -> bool:
        if not referral_token or settings.partizan_self_dogfood_product_id is None:
            return False
        experiment = self._resolve_configured_experiment(referral_token, settings)
        binding = SelfDogfoodProjectBinding(
            project_id=project_id,
            product_id=experiment.product_id,
            experiment_id=experiment.id,
            referral_token=referral_token,
            created_at=datetime.now(UTC),
        )
        return self._store.put_if_absent(
            SELF_DOGFOOD_ATTRIBUTION_NAMESPACE,
            str(project_id),
            binding.model_dump(mode="json"),
        )

    def bind_project_best_effort(
        self,
        project_id: UUID,
        referral_token: str | None,
        settings: Settings,
    ) -> bool:
        try:
            return self.bind_project(project_id, referral_token, settings)
        except Exception:
            _logger.warning(
                "self_dogfood_project_binding_failed",
                extra={"project_id": str(project_id)},
                exc_info=True,
            )
            return False

    def project_binding(self, project_id: UUID) -> SelfDogfoodProjectBinding | None:
        payload = self._store.get(SELF_DOGFOOD_ATTRIBUTION_NAMESPACE, str(project_id))
        if payload is None:
            return None
        return SelfDogfoodProjectBinding.model_validate(payload)

    def record_project_event(
        self,
        project_id: UUID,
        *,
        event_type: str,
        business_key: str,
        settings: Settings,
        revenue: float = 0,
    ) -> bool:
        binding = self.project_binding(project_id)
        configured = settings.partizan_self_dogfood_product_id
        if binding is None or configured is None or binding.product_id != configured:
            return False
        experiment = distribution_execution_service.get_experiment(binding.experiment_id)
        if experiment.product_id != configured:
            raise ValueError("Self-dogfood project attribution no longer matches configured product")
        event_id = uuid5(
            _SELF_DOGFOOD_EVENT_NAMESPACE,
            f"{project_id}:{event_type}:{business_key}",
        )
        distribution_analytics_service.ingest_event(
            DistributionAnalyticsEventCreate(
                event_id=event_id,
                event_type=event_type,
                experiment_id=binding.experiment_id,
                actor_id=f"partizan-project:{project_id}",
                revenue=revenue,
                properties={"source": "PARTIZAN_SELF_DOGFOOD"},
            )
        )
        return True

    def record_project_event_best_effort(
        self,
        project_id: UUID,
        *,
        event_type: str,
        business_key: str,
        settings: Settings,
        revenue: float = 0,
    ) -> bool:
        try:
            return self.record_project_event(
                project_id,
                event_type=event_type,
                business_key=business_key,
                settings=settings,
                revenue=revenue,
            )
        except Exception:
            _logger.warning(
                "self_dogfood_event_ingest_failed",
                extra={"project_id": str(project_id), "event_type": event_type},
                exc_info=True,
            )
            return False

    def snapshot(self, settings: Settings | None = None) -> SelfDogfoodSnapshot:
        settings = settings or get_settings()
        product_id = settings.partizan_self_dogfood_product_id
        blockers: list[str] = []
        if product_id is None:
            blockers.append("PARTIZAN_SELF_DOGFOOD_PRODUCT_ID is not configured")
            return SelfDogfoodSnapshot(blockers=blockers)

        try:
            product_intake_service.get_product(product_id)
        except KeyError:
            blockers.append("Configured self-dogfood product does not exist")
            return SelfDogfoodSnapshot(
                configured_product_id=product_id,
                blockers=blockers,
            )

        experiments = distribution_execution_service.list_experiments(product_id)
        measurable = [
            item
            for item in experiments
            if item.status
            in {
                DistributionExperimentStatus.RUNNING,
                DistributionExperimentStatus.FINISHED,
            }
        ]
        running = [
            item for item in experiments if item.status == DistributionExperimentStatus.RUNNING
        ]
        analytics = distribution_analytics_service.product_analytics(product_id)
        visits = sum(item.metrics.visits for item in analytics.experiments)
        signups = sum(item.metrics.signups for item in analytics.experiments)
        activated = sum(item.metrics.activated_users for item in analytics.experiments)
        paid = sum(item.metrics.paid_users for item in analytics.experiments)
        memory = distribution_growth_manager_service.learning_memory(product_id)
        latest_decision = memory.entries[-1].action if memory.entries else None

        if not experiments:
            blockers.append("Create a real self-dogfood DistributionExperiment")
        if not measurable:
            blockers.append("Take a self-dogfood experiment to RUNNING")
        if visits <= 0:
            blockers.append("Receive at least one real tracked VISIT")
        if signups <= 0:
            blockers.append("Receive at least one real SIGNUP")
        if activated <= 0:
            blockers.append("Receive at least one real ACTIVATED event")
        if paid <= 0:
            blockers.append("Receive at least one real paid Acquisition Plan conversion")
        if analytics.total_spend <= 0:
            blockers.append("Record real experiment spend before CAC can be calculated")
        if analytics.blended_cac is None:
            blockers.append("CAC is not calculable yet")
        if not memory.entries:
            blockers.append("Run Growth Manager after real economics arrive")

        return SelfDogfoodSnapshot(
            configured_product_id=product_id,
            product_exists=True,
            experiment_count=len(experiments),
            measurable_experiments=len(measurable),
            running_experiments=len(running),
            visits=visits,
            signups=signups,
            activated_users=activated,
            paid_users=paid,
            spend_usd=analytics.total_spend,
            revenue_usd=analytics.total_revenue,
            cac_usd=analytics.blended_cac,
            roas=analytics.blended_roas,
            learning_entries=len(memory.entries),
            latest_decision=latest_decision,
            blockers=blockers,
            proof_ready=(
                visits > 0
                and signups > 0
                and activated > 0
                and paid > 0
                and analytics.total_spend > 0
                and analytics.blended_cac is not None
                and bool(memory.entries)
            ),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(SELF_DOGFOOD_ATTRIBUTION_NAMESPACE)

    @staticmethod
    def _origin(value: str) -> tuple[str, str]:
        parts = urlsplit(value)
        return parts.scheme.lower(), parts.netloc.lower()

    @staticmethod
    def _resolve_configured_experiment(
        referral_token: str,
        settings: Settings,
    ) -> DistributionExperimentView:
        configured = settings.partizan_self_dogfood_product_id
        if configured is None:
            raise ValueError("Self-dogfood product is not configured")
        experiment, _ = distribution_execution_service.resolve_experiment(
            referral_token=referral_token
        )
        if experiment.product_id != configured:
            raise ValueError("Referral does not belong to the configured self-dogfood product")
        if experiment.status not in {
            DistributionExperimentStatus.RUNNING,
            DistributionExperimentStatus.FINISHED,
        }:
            raise ValueError("Self-dogfood attribution requires a measurable experiment")
        return experiment


self_dogfood_service = SelfDogfoodService()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect the real first-party Partizan-on-Partizan dogfood funnel."
    )
    parser.add_argument(
        "--require-proof",
        action="store_true",
        help="Exit non-zero until VISIT→SIGNUP→ACTIVATED→PAID, CAC and learning are real.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        snapshot = self_dogfood_service.snapshot()
        print(snapshot.model_dump_json())
        if args.require_proof and not snapshot.proof_ready:
            return 3
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:2000]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
