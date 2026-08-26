from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from uuid import UUID

from app.autonomous_controlled_growth import (
    AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY,
    AutonomousGrowthSweepAlreadyRunning,
)
from app.database_advisory_lock import postgres_session_advisory_lock
from app.execution_adapters import AdapterExecutionOutcome, DistributionAdapterExecuteRequest
from app.growth_autoresearch_execution import (
    _TERMINAL,
    GrowthAutoResearchExecutionService,
)
from app.growth_autoresearch_execution_schemas import (
    GrowthAutoResearchExecutionStatus,
    GrowthAutoResearchExecutionSweepView,
)
from app.growth_autoresearch_schemas import GrowthResearchTrialStatus
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)

ExecutionLockFactory = Callable[[], AbstractContextManager[bool]]


class ResumableGrowthAutoResearchExecutionService(GrowthAutoResearchExecutionService):
    """Run resumable live AutoResearch under the shared autonomous-growth lock."""

    def __init__(
        self,
        *,
        sweep_lock_factory: ExecutionLockFactory | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sweep_lock_factory = sweep_lock_factory or (
            lambda: postgres_session_advisory_lock(AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY)
        )

    async def run_once(
        self,
        *,
        product_id: UUID | None = None,
    ) -> GrowthAutoResearchExecutionSweepView:
        if self.store.ephemeral:
            return await self._run_once_unlocked(product_id=product_id)

        with self._sweep_lock_factory() as acquired:
            if not acquired:
                raise AutonomousGrowthSweepAlreadyRunning(
                    "An autonomous-growth sweep is already running in another process"
                )
            return await self._run_once_unlocked(product_id=product_id)

    async def execute_trial(self, trial_id: UUID):
        if self.store.ephemeral:
            return await self._execute_trial_unlocked(trial_id)

        with self._sweep_lock_factory() as acquired:
            if not acquired:
                raise AutonomousGrowthSweepAlreadyRunning(
                    "An autonomous-growth sweep is already running in another process"
                )
            return await self._execute_trial_unlocked(trial_id)

    async def _run_once_unlocked(
        self,
        *,
        product_id: UUID | None = None,
    ) -> GrowthAutoResearchExecutionSweepView:
        product_ids = [product_id] if product_id is not None else self._configured_product_ids()
        results = []
        for candidate_product_id in product_ids:
            history = self._autoresearch.history(candidate_product_id)
            ready = [
                trial
                for trial in history.trials
                if trial.status == GrowthResearchTrialStatus.READY
            ]
            if not ready:
                continue
            trial = ready[-1]
            existing = self.get_for_trial(trial.id)
            if existing is not None and existing.status in _TERMINAL:
                continue
            try:
                results.append(await self._execute_trial_unlocked(trial.id))
            except (KeyError, RuntimeError, ValueError) as exc:
                results.append(
                    self._record(
                        trial=trial,
                        status=GrowthAutoResearchExecutionStatus.ERROR,
                        platform=trial.challenger.platform,
                        reasons=[str(exc)[:1000]],
                        existing=existing,
                    )
                )

        return GrowthAutoResearchExecutionSweepView(
            product_id=product_id,
            attempted_count=len(results),
            executed_count=sum(item.status == "EXECUTED" for item in results),
            blocked_count=sum(
                item.status
                in {
                    GrowthAutoResearchExecutionStatus.BLOCKED,
                    GrowthAutoResearchExecutionStatus.UNAVAILABLE,
                }
                for item in results
            ),
            executions=results,
            created_at=datetime.now(UTC),
        )

    async def _execute_trial_unlocked(self, trial_id: UUID):
        trial = self._autoresearch.get_trial(trial_id)
        if trial.status != GrowthResearchTrialStatus.READY:
            return await super().execute_trial(trial_id)

        policy = self._autoresearch.get_policy(trial.product_id)
        existing = self.get_for_trial(trial.id)
        if existing is not None and existing.status in _TERMINAL:
            return existing
        if (
            existing is not None
            and existing.status == GrowthAutoResearchExecutionStatus.PREPARING
            and existing.action_id is None
        ):
            return self._record(
                trial=trial,
                status=GrowthAutoResearchExecutionStatus.ERROR,
                platform=trial.challenger.platform,
                reasons=[
                    "A previous AutoResearch preparation was interrupted before its action link "
                    "was durably confirmed. Automatic retry is blocked to avoid a duplicate "
                    "external action; reconcile the existing DistributionAction state first."
                ],
                existing=existing,
            )
        if (
            existing is not None
            and existing.status == GrowthAutoResearchExecutionStatus.IN_PROGRESS
        ):
            return self._resume_provider_reconciliation(trial, existing)
        if policy.paused:
            return self._record(
                trial=trial,
                status=GrowthAutoResearchExecutionStatus.PAUSED,
                platform=trial.challenger.platform,
                reasons=[
                    "Growth AutoResearch is paused. The READY trial and any prepared linkage are "
                    "preserved so execution can continue after Resume."
                ],
                existing=existing,
            )

        if existing is None or (
            existing.status == GrowthAutoResearchExecutionStatus.PAUSED
            and existing.action_id is None
        ):
            existing = self._record(
                trial=trial,
                status=GrowthAutoResearchExecutionStatus.PREPARING,
                platform=trial.challenger.platform,
                reasons=[
                    "Reserved this AutoResearch trial before creating a DistributionAction."
                ],
                existing=existing,
            )

        return await super().execute_trial(trial_id)

    def _resume_provider_reconciliation(self, trial, existing):
        if (
            existing.platform != "TIKTOK"
            or existing.action_type != "ORGANIC_VIDEO"
            or existing.action_id is None
        ):
            return self._record(
                trial=trial,
                status=GrowthAutoResearchExecutionStatus.ERROR,
                platform=existing.platform,
                reasons=[
                    "Only an already-submitted TikTok ORGANIC_VIDEO action may use automatic "
                    "provider reconciliation from an IN_PROGRESS AutoResearch state."
                ],
                existing=existing,
            )

        execution = self._adapter_service.execute(
            existing.action_id,
            DistributionAdapterExecuteRequest(retry=True),
        )
        return self._record(
            trial=trial,
            status=self._adapter_status(execution.receipt.outcome),
            platform=existing.platform,
            action_type=existing.action_type,
            action_id=execution.plan.action.id,
            experiment_id=execution.plan.experiment.id,
            adapter_outcome=execution.receipt.outcome.value,
            reasons=[execution.receipt.message],
            existing=existing,
        )

    @staticmethod
    def _adapter_status(outcome: AdapterExecutionOutcome) -> GrowthAutoResearchExecutionStatus:
        if outcome == AdapterExecutionOutcome.IN_PROGRESS:
            return GrowthAutoResearchExecutionStatus.IN_PROGRESS
        return GrowthAutoResearchExecutionService._adapter_status(outcome)


growth_autoresearch_execution_runtime_service = ResumableGrowthAutoResearchExecutionService(
    adapter_service=organic_creative_distribution_execution_adapter_service,
)
