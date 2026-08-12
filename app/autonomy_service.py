from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.autonomy_schemas import (
    AutonomyDecision,
    AutonomyEvaluationRequest,
    AutonomyEvaluationView,
    GrowthMandateStatus,
    GrowthMandateUpsertRequest,
    GrowthMandateView,
)
from app.distribution_analytics_service import DISTRIBUTION_SPEND_NAMESPACE
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    distribution_execution_service,
)
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    DistributionActionType,
    DistributionPlatform,
    is_valid_action_type,
)
from app.paid_lifecycle_audit import PaidLifecycleState, paid_lifecycle_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_MANDATE_NAMESPACE = "growth_mandate"


class GrowthMandateService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._mandates: dict[UUID, GrowthMandateView] = {}

    def upsert(
        self,
        product_id: UUID,
        payload: GrowthMandateUpsertRequest,
    ) -> GrowthMandateView:
        now = datetime.now(UTC)
        existing = self._get_optional(product_id)
        if existing is None or existing.status == GrowthMandateStatus.REVOKED:
            mandate_id = uuid4()
            created_at = now
            status = GrowthMandateStatus.ACTIVE
            version = (existing.version + 1) if existing is not None else 1
        else:
            mandate_id = existing.id
            created_at = existing.created_at
            status = existing.status
            version = existing.version + 1

        mandate = GrowthMandateView(
            id=mandate_id,
            product_id=product_id,
            version=version,
            status=status,
            total_budget_cap=payload.total_budget_cap,
            target_max_cac=payload.target_max_cac,
            max_autonomous_spend_per_experiment=(
                payload.max_autonomous_spend_per_experiment
            ),
            max_autonomous_spend_per_day=payload.max_autonomous_spend_per_day,
            max_concurrent_running_experiments=(
                payload.max_concurrent_running_experiments
            ),
            allowed_platforms=sorted(
                set(payload.allowed_platforms),
                key=lambda item: item.value,
            ),
            allowed_actions=sorted(
                set(payload.allowed_actions),
                key=lambda item: item.value,
            ),
            autonomous_prepare=payload.autonomous_prepare,
            autonomous_approve=payload.autonomous_approve,
            autonomous_paid_activation=payload.autonomous_paid_activation,
            approval_threshold=payload.approval_threshold,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            created_at=created_at,
            updated_at=now,
        )
        self._mandates[product_id] = mandate
        self._persist(mandate)
        return mandate

    def get(self, product_id: UUID) -> GrowthMandateView:
        mandate = self._get_optional(product_id)
        if mandate is None:
            raise KeyError(product_id)
        return mandate

    def set_status(
        self,
        product_id: UUID,
        status: GrowthMandateStatus,
    ) -> GrowthMandateView:
        mandate = self.get(product_id)
        if mandate.status == GrowthMandateStatus.REVOKED:
            raise ValueError("A REVOKED Growth Mandate cannot be reactivated or paused")
        if mandate.status == status:
            return mandate
        updated = mandate.model_copy(
            update={
                "status": status,
                "version": mandate.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._mandates[product_id] = updated
        self._persist(updated)
        return updated

    def evaluate(
        self,
        product_id: UUID,
        payload: AutonomyEvaluationRequest,
    ) -> AutonomyEvaluationView:
        mandate = self._get_optional(product_id)
        if mandate is None:
            return AutonomyEvaluationView(
                decision=AutonomyDecision.BLOCK,
                reasons=["No Growth Mandate is configured for this product"],
            )

        total_spend = self._current_total_spend(product_id)
        daily_spend = self._current_daily_spend(product_id)
        running = self._running_experiment_count(product_id)
        remaining_total = max(round(mandate.total_budget_cap - total_spend, 2), 0)
        remaining_daily = max(
            round(mandate.max_autonomous_spend_per_day - daily_spend, 2),
            0,
        )

        blocks: list[str] = []
        approvals: list[str] = []
        now = datetime.now(UTC)

        if mandate.status != GrowthMandateStatus.ACTIVE:
            blocks.append(f"Growth Mandate is {mandate.status.value}")
        if (
            mandate.effective_from is not None
            and now < self._as_utc(mandate.effective_from)
        ):
            blocks.append("Growth Mandate is not effective yet")
        if (
            mandate.effective_until is not None
            and now >= self._as_utc(mandate.effective_until)
        ):
            blocks.append("Growth Mandate has expired")
        if payload.platform not in mandate.allowed_platforms:
            blocks.append(
                f"Platform {payload.platform.value} is not allowed by the Growth Mandate"
            )
        if payload.action_type not in mandate.allowed_actions:
            blocks.append(
                f"Action {payload.action_type.value} is not allowed by the Growth Mandate"
            )
        if not is_valid_action_type(payload.platform, payload.action_type):
            blocks.append(
                f"Action {payload.action_type.value} is not valid for "
                f"{payload.platform.value}"
            )
        if self._paid_reconciliation_required(product_id):
            blocks.append(
                "A paid provider action requires reconciliation before autonomous mutations"
            )
        if payload.requests_paid_activation:
            if payload.action_type != DistributionActionType.PAID_CAMPAIGN:
                blocks.append(
                    "Paid activation can only be requested for PAID_CAMPAIGN actions"
                )
            if payload.proposed_budget <= 0:
                blocks.append("Paid activation requires a positive proposed budget")

        if total_spend + payload.proposed_budget > mandate.total_budget_cap:
            blocks.append(
                "Proposed spend would exceed the total Growth Mandate budget cap"
            )
        if daily_spend + payload.proposed_budget > mandate.max_autonomous_spend_per_day:
            blocks.append("Proposed spend would exceed the daily autonomous spend cap")
        if (
            mandate.max_concurrent_running_experiments is not None
            and running >= mandate.max_concurrent_running_experiments
        ):
            blocks.append("Maximum concurrent RUNNING experiments has been reached")

        if payload.requires_prepare and not mandate.autonomous_prepare:
            approvals.append("Autonomous preparation is not delegated")
        if payload.requires_approval and not mandate.autonomous_approve:
            approvals.append("Autonomous action approval is not delegated")
        if payload.requests_paid_activation and not mandate.autonomous_paid_activation:
            approvals.append("Autonomous paid activation is not delegated")
        if payload.proposed_budget > mandate.max_autonomous_spend_per_experiment:
            approvals.append("Proposed spend exceeds the autonomous per-experiment cap")
        if (
            mandate.approval_threshold is not None
            and payload.proposed_budget > mandate.approval_threshold
        ):
            approvals.append("Proposed spend exceeds the human approval threshold")

        if blocks:
            decision = AutonomyDecision.BLOCK
            reasons = blocks + approvals
        elif approvals:
            decision = AutonomyDecision.REQUIRE_APPROVAL
            reasons = approvals
        else:
            decision = AutonomyDecision.ALLOW
            reasons = ["Action is within the active Growth Mandate"]

        return AutonomyEvaluationView(
            decision=decision,
            reasons=reasons,
            mandate_id=mandate.id,
            mandate_version=mandate.version,
            current_total_spend=total_spend,
            current_daily_spend=daily_spend,
            remaining_total_budget=remaining_total,
            remaining_daily_budget=remaining_daily,
            running_experiments=running,
        )

    def _get_optional(self, product_id: UUID) -> GrowthMandateView | None:
        cached = self._mandates.get(product_id)
        if cached is not None:
            return cached
        stored = self._store.get(GROWTH_MANDATE_NAMESPACE, str(product_id))
        if stored is None:
            return None
        mandate = GrowthMandateView.model_validate(stored)
        self._mandates[product_id] = mandate
        return mandate

    def _persist(self, mandate: GrowthMandateView) -> None:
        self._store.put(
            GROWTH_MANDATE_NAMESPACE,
            str(mandate.product_id),
            mandate.model_dump(mode="json"),
        )

    def _current_total_spend(self, product_id: UUID) -> float:
        experiment_ids = {
            experiment.id
            for experiment in distribution_execution_service.list_experiments(product_id)
        }
        total = 0.0
        for row in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE):
            try:
                experiment_id = UUID(str(row["experiment_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if experiment_id in experiment_ids:
                total += float(row.get("amount", 0))
        return round(total, 2)

    def _current_daily_spend(self, product_id: UUID) -> float:
        experiment_ids = {
            experiment.id
            for experiment in distribution_execution_service.list_experiments(product_id)
        }
        today = datetime.now(UTC).date()
        total = 0.0
        for row in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE):
            try:
                experiment_id = UUID(str(row["experiment_id"]))
                occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if experiment_id not in experiment_ids:
                continue
            if self._as_utc(occurred_at).date() == today:
                total += float(row.get("amount", 0))
        return round(total, 2)

    def _running_experiment_count(self, product_id: UUID) -> int:
        return sum(
            experiment.status == DistributionExperimentStatus.RUNNING
            for experiment in distribution_execution_service.list_experiments(product_id)
        )

    def _paid_reconciliation_required(self, product_id: UUID) -> bool:
        for row in self._store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE):
            try:
                action = DistributionActionView.model_validate(row)
            except ValueError:
                continue
            if action.action_type != DistributionActionType.PAID_CAMPAIGN:
                continue
            if action.platform not in {
                DistributionPlatform.INSTAGRAM,
                DistributionPlatform.TIKTOK,
            }:
                continue
            if action.experiment_id is None:
                return True
            try:
                experiment = distribution_execution_service.get_experiment(
                    action.experiment_id
                )
            except KeyError:
                return True
            if experiment.product_id != product_id:
                continue
            lifecycle = paid_lifecycle_service.get(action.id)
            if lifecycle.requires_reconciliation or lifecycle.state in {
                PaidLifecycleState.ACTIVATION_ATTEMPTED,
                PaidLifecycleState.RECONCILIATION_REQUIRED,
                PaidLifecycleState.UNKNOWN,
            }:
                return True
        return False

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def reset(self) -> None:
        self._mandates.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(GROWTH_MANDATE_NAMESPACE)


growth_mandate_service = GrowthMandateService()
