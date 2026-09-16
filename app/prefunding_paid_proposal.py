from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store

PREFUNDING_PAID_PROPOSAL_NAMESPACE = "prefunding_paid_proposal"


class PrefundingPaidProposal(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    play_id: UUID
    platform: DistributionPlatform
    opportunity_title: str
    hypothesis: str
    success_metric: str
    budget_cap: float = Field(gt=0)
    created_at: datetime


class PrefundingPaidProposalService:
    """Persist a concrete paid proposal that has no execution semantics."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def get_or_create(
        self,
        *,
        project_id: UUID,
        product_id: UUID,
        project_budget_usd: float,
        plays: list[DistributionPlayView],
    ) -> PrefundingPaidProposal:
        play, budget_cap = self._candidate(
            project_budget_usd=project_budget_usd,
            plays=plays,
        )
        existing = self._existing(project_id, product_id)
        if (
            existing is not None
            and existing.play_id == play.id
            and float(existing.budget_cap) == budget_cap
        ):
            return existing

        proposal = PrefundingPaidProposal(
            id=uuid4(),
            project_id=project_id,
            product_id=product_id,
            play_id=play.id,
            platform=play.platform,
            opportunity_title=play.opportunity_title,
            hypothesis=play.hypothesis,
            success_metric=play.success_metric,
            budget_cap=budget_cap,
            created_at=datetime.now(UTC),
        )
        self._store.put(
            PREFUNDING_PAID_PROPOSAL_NAMESPACE,
            str(proposal.id),
            proposal.model_dump(mode="json"),
        )
        return proposal

    def get(self, proposal_id: UUID) -> PrefundingPaidProposal:
        payload = self._store.get(PREFUNDING_PAID_PROPOSAL_NAMESPACE, str(proposal_id))
        if payload is None:
            raise KeyError(proposal_id)
        return PrefundingPaidProposal.model_validate(payload)

    def require_project(self, proposal_id: UUID, project_id: UUID) -> PrefundingPaidProposal:
        proposal = self.get(proposal_id)
        if proposal.project_id != project_id:
            raise ValueError("Paid funding proposal does not belong to this project")
        return proposal

    def _candidate(
        self,
        *,
        project_budget_usd: float,
        plays: list[DistributionPlayView],
    ) -> tuple[DistributionPlayView, float]:
        candidates = [
            play
            for play in plays
            if play.status == DistributionPlayStatus.READY
            and play.tactic_class == DistributionTacticClass.PAID_PLATFORM
            and play.action_type == DistributionActionType.PAID_CAMPAIGN
            and play.estimated_cost_max > 0
        ]
        if not candidates:
            raise ValueError("No executable paid distribution play is ready for funding")
        candidates.sort(key=lambda play: (-play.priority_score, str(play.id)))
        play = candidates[0]
        budget_cap = round(min(float(project_budget_usd), float(play.estimated_cost_max)), 2)
        if budget_cap <= 0:
            raise ValueError("Customer test budget must be positive before paid funding")
        return play, budget_cap

    def _existing(
        self,
        project_id: UUID,
        product_id: UUID,
    ) -> PrefundingPaidProposal | None:
        matches = [
            PrefundingPaidProposal.model_validate(item)
            for item in self._store.list_namespace(PREFUNDING_PAID_PROPOSAL_NAMESPACE)
            if str(item.get("project_id") or "") == str(project_id)
            and str(item.get("product_id") or "") == str(product_id)
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.created_at, str(item.id)))
        return matches[-1]

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(PREFUNDING_PAID_PROPOSAL_NAMESPACE)


prefunding_paid_proposal_service = PrefundingPaidProposalService()
