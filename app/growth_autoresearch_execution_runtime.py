from __future__ import annotations

from uuid import UUID

from app.growth_autoresearch_execution import GrowthAutoResearchExecutionService
from app.growth_autoresearch_execution_schemas import GrowthAutoResearchExecutionStatus
from app.growth_autoresearch_schemas import GrowthResearchTrialStatus


class ResumableGrowthAutoResearchExecutionService(GrowthAutoResearchExecutionService):
    """Keep an existing READY trial resumable while AutoResearch is paused."""

    async def execute_trial(self, trial_id: UUID):
        trial = self._autoresearch.get_trial(trial_id)
        if trial.status != GrowthResearchTrialStatus.READY:
            return await super().execute_trial(trial_id)
        policy = self._autoresearch.get_policy(trial.product_id)
        if not policy.paused:
            return await super().execute_trial(trial_id)

        existing = self.get_for_trial(trial.id)
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


growth_autoresearch_execution_runtime_service = ResumableGrowthAutoResearchExecutionService()
