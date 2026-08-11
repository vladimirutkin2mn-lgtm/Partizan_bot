from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.distribution_schemas import DistributionOpportunityView

PolicyState = Literal["ALLOWED", "DISALLOWED", "UNKNOWN"]
DisclosureState = Literal["REQUIRED", "NOT_REQUIRED", "UNKNOWN"]


class CommunityPolicyProposalView(BaseModel):
    opportunity_id: UUID
    commercial_participation: PolicyState = "UNKNOWN"
    self_promotion: PolicyState = "UNKNOWN"
    links: PolicyState = "UNKNOWN"
    product_mentions: PolicyState = "UNKNOWN"
    standalone_posts: PolicyState = "UNKNOWN"
    comments: PolicyState = "UNKNOWN"
    disclosure: DisclosureState = "UNKNOWN"
    confidence: float = Field(default=0, ge=0, le=100)
    rationale: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    generated_at: datetime

    @property
    def has_unknowns(self) -> bool:
        states = (
            self.commercial_participation,
            self.self_promotion,
            self.links,
            self.product_mentions,
            self.standalone_posts,
            self.comments,
            self.disclosure,
        )
        return "UNKNOWN" in states


class OpportunityEnrichmentView(BaseModel):
    opportunity: DistributionOpportunityView
    policy_proposal: CommunityPolicyProposalView | None = None
    new_evidence_count: int = Field(ge=0)
    partial_failure: bool = False
    failure_reason: str | None = None


class ProductOpportunityEnrichmentView(BaseModel):
    product_id: UUID
    requested_count: int = Field(ge=0)
    enriched_count: int = Field(ge=0)
    partial_failure_count: int = Field(ge=0)
    results: list[OpportunityEnrichmentView] = Field(default_factory=list)
