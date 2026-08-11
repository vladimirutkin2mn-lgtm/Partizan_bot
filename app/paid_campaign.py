from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_schemas import DistributionTacticClass
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

PAID_CAMPAIGN_SPEC_NAMESPACE = "paid_campaign_spec"


class PaidCampaignLaunchMode(StrEnum):
    CREATE_PAUSED = "CREATE_PAUSED"


class PaidCampaignObjective(StrEnum):
    ACQUISITION = "ACQUISITION"


class PaidCampaignSpec(BaseModel):
    action_id: UUID
    experiment_id: UUID
    product_id: UUID
    play_id: UUID
    opportunity_id: UUID
    platform: DistributionPlatform
    tactic_id: str = Field(min_length=1, max_length=120)
    launch_mode: PaidCampaignLaunchMode = PaidCampaignLaunchMode.CREATE_PAUSED
    objective: PaidCampaignObjective = PaidCampaignObjective.ACQUISITION
    optimization_event: str = Field(default="PAID", min_length=1, max_length=80)
    destination_url: HttpUrl
    budget_cap: float = Field(gt=0)
    target_cac: float | None = Field(default=None, gt=0)
    audience: dict = Field(default_factory=dict)
    creative_brief: dict = Field(default_factory=dict)
    success_metric: str = Field(min_length=3, max_length=1000)
    kill_criteria: str = Field(min_length=5, max_length=1500)
    provider_metadata: dict = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def validate_provider_safe_state(self) -> PaidCampaignSpec:
        if self.launch_mode != PaidCampaignLaunchMode.CREATE_PAUSED:
            raise ValueError("MVP paid campaign specs may only be created in paused mode")
        if not self.audience:
            raise ValueError("Paid campaign spec requires audience evidence")
        if not self.creative_brief:
            raise ValueError("Paid campaign spec requires a creative brief")
        return self


class PaidCampaignSpecService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def ensure(self, action_id: UUID) -> PaidCampaignSpec:
        existing = self.get(action_id)
        if existing is not None:
            return existing

        action = distribution_execution_service.get_action(action_id)
        if action.action_type != DistributionActionType.PAID_CAMPAIGN:
            raise ValueError("PaidCampaignSpec can only be created for PAID_CAMPAIGN actions")
        if action.experiment_id is None:
            raise ValueError("Paid campaign action has no DistributionExperiment")

        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        product = product_intake_service.get_product(experiment.product_id)
        play = distribution_play_service.find(product.id, experiment.distribution_play_id)
        if play.tactic_class != DistributionTacticClass.PAID_PLATFORM:
            raise ValueError("DistributionPlay is not a paid-platform tactic")
        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        icp = next(
            (item for item in icp_service.get(product.id).icps if item.id == play.icp_id),
            None,
        )
        if icp is None:
            raise ValueError("Paid campaign ICP could not be resolved")

        destination = action.tracking_url
        if destination is None:
            raise ValueError("Paid campaign action requires an attributed tracking destination")

        budget_cap = self._budget_cap(product.budget, play.estimated_cost_max)
        target_cac = product.max_cac if product.max_cac and product.max_cac > 0 else None
        spec = PaidCampaignSpec(
            action_id=action.id,
            experiment_id=experiment.id,
            product_id=product.id,
            play_id=play.id,
            opportunity_id=opportunity.id,
            platform=action.platform,
            tactic_id=play.tactic_id,
            launch_mode=PaidCampaignLaunchMode.CREATE_PAUSED,
            destination_url=destination,
            budget_cap=budget_cap,
            target_cac=target_cac,
            audience=self._audience(play.platform, opportunity, icp.model_dump(mode="json")),
            creative_brief={
                "product_name": product.name,
                "value_proposition": product.value_proposition or product.description,
                "message_hook": icp.message_hook,
                "pain": icp.pain,
                "desired_outcome": icp.desired_outcome,
                "cta": "Use the attributed destination URL",
                "constraints": list(product.constraints),
            },
            success_metric=play.success_metric,
            kill_criteria=self._kill_criteria(budget_cap, target_cac),
            provider_metadata={},
            created_at=datetime.now(UTC),
        )
        self._store.put(
            PAID_CAMPAIGN_SPEC_NAMESPACE,
            str(action.id),
            spec.model_dump(mode="json"),
        )
        return spec

    def get(self, action_id: UUID) -> PaidCampaignSpec | None:
        payload = self._store.get(PAID_CAMPAIGN_SPEC_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return PaidCampaignSpec.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(PAID_CAMPAIGN_SPEC_NAMESPACE)

    def _budget_cap(self, product_budget: float | None, play_budget: float) -> float:
        caps = [float(play_budget)]
        if product_budget is not None and product_budget > 0:
            caps.append(float(product_budget))
        cap = min(caps)
        if cap <= 0:
            raise ValueError("Paid campaign budget cap must be positive")
        return round(cap, 2)

    def _audience(self, platform, opportunity, icp: dict) -> dict:
        base = {
            "icp": {
                "title": icp.get("title"),
                "pain": icp.get("pain"),
                "trigger": icp.get("trigger"),
            },
            "opportunity": {
                "title": opportunity.title,
                "canonical_key": opportunity.canonical_key,
                "kind": opportunity.kind.value,
                "relevance_score": opportunity.relevance_score,
            },
            "evidence": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("snippet"),
                }
                for item in opportunity.evidence[:5]
                if isinstance(item, dict)
            ],
        }
        metadata = opportunity.metadata
        if platform == DistributionPlatform.TELEGRAM:
            base["platform_signals"] = {
                "surface": opportunity.kind.value,
                "handle": metadata.get("handle"),
                "topic": metadata.get("topic"),
            }
        elif platform == DistributionPlatform.INSTAGRAM:
            base["platform_signals"] = {
                "creator": metadata.get("handle") or opportunity.canonical_key,
                "topic": metadata.get("topic"),
            }
        elif platform == DistributionPlatform.REDDIT:
            base["platform_signals"] = {
                "subreddit": metadata.get("subreddit") or opportunity.canonical_key,
                "topic": metadata.get("topic"),
            }
        elif platform == DistributionPlatform.TIKTOK:
            base["platform_signals"] = {
                "cluster": opportunity.canonical_key,
                "topic": metadata.get("topic"),
                "hashtags": metadata.get("hashtags", []),
                "creators": metadata.get("creators", []),
            }
        else:
            raise ValueError(f"Unsupported paid platform: {platform.value}")
        return base

    def _kill_criteria(self, budget_cap: float, target_cac: float | None) -> str:
        if target_cac is None:
            return (
                f"Keep campaign paused until explicit activation; after launch, stop before spend "
                f"exceeds the approved test cap of {budget_cap:.2f} without sufficient paid signal."
            )
        no_paid_limit = min(budget_cap, 3 * target_cac)
        return (
            "Keep campaign paused until explicit activation. After launch, stop or review if spend "
            f"reaches {no_paid_limit:.2f} (up to 3x target CAC) with zero paid users; never exceed "
            f"the approved test cap of {budget_cap:.2f}."
        )


paid_campaign_spec_service = PaidCampaignSpecService()
