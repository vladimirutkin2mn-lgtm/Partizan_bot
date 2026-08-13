from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateStatus
from app.autonomy_service import growth_mandate_service
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionType
from app.outreach_briefs import (
    OUTREACH_BRIEF_NAMESPACE,
    OutreachBriefCreateRequest,
    OutreachBriefView,
    outreach_brief_service,
)
from app.outreach_sender import outreach_sender_service
from app.outreach_targets import (
    OutreachContactProvenanceType,
    OutreachTargetType,
    OutreachTargetView,
    outreach_target_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

OUTREACH_POLICY_NAMESPACE = "outreach_policy"
HARD_MAX_PREPARED_PER_DAY = 10
HARD_MAX_INITIAL_SENDS_PER_DAY = 5
HARD_MAX_INITIAL_SENDS_PER_DOMAIN_PER_DAY = 1


class OutreachPolicyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class OutreachPolicyUpsertRequest(BaseModel):
    minimum_target_confidence: float = Field(default=75, ge=60, le=100)
    allowed_target_types: list[OutreachTargetType] = Field(min_length=1)
    allowed_contact_provenance: list[OutreachContactProvenanceType] = Field(min_length=1)
    max_prepared_per_day: int = Field(default=3, ge=1, le=HARD_MAX_PREPARED_PER_DAY)
    max_prepared_per_domain_per_day: int = Field(default=1, ge=1, le=2)
    max_initial_sends_per_day: int = Field(
        default=3,
        ge=1,
        le=HARD_MAX_INITIAL_SENDS_PER_DAY,
    )
    max_initial_sends_per_domain_per_day: int = Field(
        default=1,
        ge=1,
        le=HARD_MAX_INITIAL_SENDS_PER_DOMAIN_PER_DAY,
    )
    target_cooldown_days: int = Field(default=30, ge=30, le=365)
    domain_cooldown_hours: int = Field(default=24, ge=24, le=720)
    require_sender_ready_before_prepare: bool = True

    @model_validator(mode="after")
    def validate_caps(self) -> OutreachPolicyUpsertRequest:
        if self.max_prepared_per_domain_per_day > self.max_prepared_per_day:
            raise ValueError(
                "max_prepared_per_domain_per_day cannot exceed max_prepared_per_day"
            )
        if self.max_initial_sends_per_domain_per_day > self.max_initial_sends_per_day:
            raise ValueError(
                "max_initial_sends_per_domain_per_day cannot exceed max_initial_sends_per_day"
            )
        return self


class OutreachPolicyStatusRequest(BaseModel):
    status: OutreachPolicyStatus


class OutreachPolicyView(BaseModel):
    id: UUID
    product_id: UUID
    version: int = Field(ge=1)
    status: OutreachPolicyStatus
    growth_mandate_id: UUID
    growth_mandate_version: int = Field(ge=1)
    minimum_target_confidence: float
    allowed_target_types: list[OutreachTargetType]
    allowed_contact_provenance: list[OutreachContactProvenanceType]
    max_prepared_per_day: int
    max_prepared_per_domain_per_day: int
    max_initial_sends_per_day: int
    max_initial_sends_per_domain_per_day: int
    target_cooldown_days: int
    domain_cooldown_hours: int
    require_sender_ready_before_prepare: bool
    max_followups: int = 0
    automatic_send_enabled: bool = False
    created_at: datetime
    updated_at: datetime


class OutreachAutonomousPreparationView(BaseModel):
    product_id: UUID
    policy_id: UUID
    policy_version: int
    prepared: bool
    target_id: UUID | None = None
    brief_id: UUID | None = None
    play_id: UUID | None = None
    action_id: UUID | None = None
    experiment_id: UUID | None = None
    platform: str | None = None
    reasons: list[str] = Field(default_factory=list)


class OutreachPolicyService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._policies: dict[UUID, OutreachPolicyView] = {}

    def upsert(
        self,
        product_id: UUID,
        payload: OutreachPolicyUpsertRequest,
    ) -> OutreachPolicyView:
        mandate = growth_mandate_service.get(product_id)
        if mandate.status != GrowthMandateStatus.ACTIVE:
            raise ValueError("An ACTIVE Growth Mandate is required for outreach policy")
        if DistributionActionType.OUTREACH_EMAIL not in mandate.allowed_actions:
            raise ValueError("Growth Mandate must explicitly allow OUTREACH_EMAIL")
        if not mandate.autonomous_prepare:
            raise ValueError("Growth Mandate must delegate autonomous preparation")

        now = datetime.now(UTC)
        existing = self._get_optional(product_id)
        if existing is None or existing.status == OutreachPolicyStatus.REVOKED:
            policy_id = uuid4()
            created_at = now
            status = OutreachPolicyStatus.ACTIVE
            version = (existing.version + 1) if existing is not None else 1
        else:
            policy_id = existing.id
            created_at = existing.created_at
            status = existing.status
            version = existing.version + 1

        policy = OutreachPolicyView(
            id=policy_id,
            product_id=product_id,
            version=version,
            status=status,
            growth_mandate_id=mandate.id,
            growth_mandate_version=mandate.version,
            minimum_target_confidence=payload.minimum_target_confidence,
            allowed_target_types=sorted(
                set(payload.allowed_target_types),
                key=lambda item: item.value,
            ),
            allowed_contact_provenance=sorted(
                set(payload.allowed_contact_provenance),
                key=lambda item: item.value,
            ),
            max_prepared_per_day=payload.max_prepared_per_day,
            max_prepared_per_domain_per_day=payload.max_prepared_per_domain_per_day,
            max_initial_sends_per_day=payload.max_initial_sends_per_day,
            max_initial_sends_per_domain_per_day=(
                payload.max_initial_sends_per_domain_per_day
            ),
            target_cooldown_days=payload.target_cooldown_days,
            domain_cooldown_hours=payload.domain_cooldown_hours,
            require_sender_ready_before_prepare=payload.require_sender_ready_before_prepare,
            created_at=created_at,
            updated_at=now,
        )
        self._policies[product_id] = policy
        self._persist(policy)
        return policy

    def get(self, product_id: UUID) -> OutreachPolicyView:
        policy = self._get_optional(product_id)
        if policy is None:
            raise KeyError(product_id)
        return policy

    def get_optional(self, product_id: UUID) -> OutreachPolicyView | None:
        return self._get_optional(product_id)

    def set_status(
        self,
        product_id: UUID,
        status: OutreachPolicyStatus,
    ) -> OutreachPolicyView:
        policy = self.get(product_id)
        if policy.status == OutreachPolicyStatus.REVOKED:
            raise ValueError("A REVOKED Outreach Policy cannot be reactivated or paused")
        if policy.status == status:
            return policy
        updated = policy.model_copy(
            update={
                "status": status,
                "version": policy.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._policies[product_id] = updated
        self._persist(updated)
        return updated

    def validate_current_mandate(self, policy: OutreachPolicyView) -> list[str]:
        try:
            mandate = growth_mandate_service.get(policy.product_id)
        except KeyError:
            return ["Growth Mandate is unavailable"]
        reasons: list[str] = []
        if mandate.status != GrowthMandateStatus.ACTIVE:
            reasons.append(f"Growth Mandate is {mandate.status.value}")
        if mandate.id != policy.growth_mandate_id or mandate.version != policy.growth_mandate_version:
            reasons.append(
                "Growth Mandate changed after the Outreach Policy was authorized; refresh policy"
            )
        if DistributionActionType.OUTREACH_EMAIL not in mandate.allowed_actions:
            reasons.append("Growth Mandate no longer allows OUTREACH_EMAIL")
        if not mandate.autonomous_prepare:
            reasons.append("Growth Mandate no longer delegates autonomous preparation")
        return reasons

    def reset(self) -> None:
        self._policies.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_POLICY_NAMESPACE)

    def _get_optional(self, product_id: UUID) -> OutreachPolicyView | None:
        cached = self._policies.get(product_id)
        if cached is not None:
            return cached
        stored = self._store.get(OUTREACH_POLICY_NAMESPACE, str(product_id))
        if stored is None:
            return None
        policy = OutreachPolicyView.model_validate(stored)
        self._policies[product_id] = policy
        return policy

    def _persist(self, policy: OutreachPolicyView) -> None:
        self._store.put(
            OUTREACH_POLICY_NAMESPACE,
            str(policy.product_id),
            policy.model_dump(mode="json"),
        )


class OutreachAutonomousPreparationService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        policy_service: OutreachPolicyService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._policy_service = policy_service or outreach_policy_service

    async def prepare_next(
        self,
        product_id: UUID,
    ) -> OutreachAutonomousPreparationView | None:
        policy = self._policy_service.get_optional(product_id)
        if policy is None:
            return None
        if policy.status != OutreachPolicyStatus.ACTIVE:
            return OutreachAutonomousPreparationView(
                product_id=product_id,
                policy_id=policy.id,
                policy_version=policy.version,
                prepared=False,
                reasons=[f"Outreach Policy is {policy.status.value}"],
            )
        mandate_reasons = self._policy_service.validate_current_mandate(policy)
        if mandate_reasons:
            return OutreachAutonomousPreparationView(
                product_id=product_id,
                policy_id=policy.id,
                policy_version=policy.version,
                prepared=False,
                reasons=mandate_reasons,
            )
        if self._has_pending_experiment(product_id):
            return None
        if policy.require_sender_ready_before_prepare:
            readiness = outreach_sender_service.readiness()
            if not readiness.ready:
                return OutreachAutonomousPreparationView(
                    product_id=product_id,
                    policy_id=policy.id,
                    policy_version=policy.version,
                    prepared=False,
                    reasons=["Outreach sender is not ready", *readiness.blockers],
                )

        candidates = outreach_target_service.list_product(product_id).targets
        blockers: list[str] = []
        for target in candidates:
            reasons = self._target_blockers(policy, target)
            if reasons:
                blockers.extend(reasons)
                continue
            evaluation = growth_mandate_service.evaluate(
                product_id,
                AutonomyEvaluationRequest(
                    platform=self._platform(target),
                    action_type=DistributionActionType.OUTREACH_EMAIL,
                    proposed_budget=0,
                    requires_prepare=True,
                    requires_approval=False,
                    requests_paid_activation=False,
                ),
            )
            if evaluation.decision != AutonomyDecision.ALLOW:
                blockers.extend(evaluation.reasons)
                continue
            brief = await outreach_brief_service.create(
                target.id,
                OutreachBriefCreateRequest(),
            )
            return OutreachAutonomousPreparationView(
                product_id=product_id,
                policy_id=policy.id,
                policy_version=policy.version,
                prepared=True,
                target_id=target.id,
                brief_id=brief.id,
                play_id=brief.distribution_play_id,
                action_id=brief.action_id,
                experiment_id=brief.experiment_id,
                platform=self._platform(target).value,
                reasons=[
                    "Prepared the highest-confidence eligible outreach target inside policy",
                    "Explicit send authorization is still required; automatic send is disabled",
                ],
            )

        return OutreachAutonomousPreparationView(
            product_id=product_id,
            policy_id=policy.id,
            policy_version=policy.version,
            prepared=False,
            reasons=self._dedupe(blockers) or ["No eligible outreach target is available"],
        )

    def _target_blockers(
        self,
        policy: OutreachPolicyView,
        target: OutreachTargetView,
    ) -> list[str]:
        reasons: list[str] = []
        if not target.executable:
            reasons.append(f"Target {target.id} is suppressed or non-executable")
        if target.confidence < policy.minimum_target_confidence:
            reasons.append(
                f"Target {target.id} confidence is below the Outreach Policy threshold"
            )
        if target.target_type not in policy.allowed_target_types:
            reasons.append(f"Target type {target.target_type.value} is not allowed")
        if target.contact_evidence.provenance_type not in policy.allowed_contact_provenance:
            reasons.append(
                f"Contact provenance {target.contact_evidence.provenance_type.value} is not allowed"
            )
        now = datetime.now(UTC)
        product_briefs = self._product_briefs(policy.product_id)
        todays = [brief for brief in product_briefs if brief.created_at.date() == now.date()]
        if len(todays) >= policy.max_prepared_per_day:
            reasons.append("Daily autonomous outreach preparation cap has been reached")
            return reasons

        target_cutoff = now - timedelta(days=policy.target_cooldown_days)
        if any(
            brief.outreach_target_id == target.id and brief.created_at >= target_cutoff
            for brief in product_briefs
        ):
            reasons.append("Target is inside the configured outreach cooling period")

        domain = self._domain(target.business_email)
        domain_today = [
            brief
            for brief in todays
            if self._brief_domain(brief) == domain
        ]
        if len(domain_today) >= policy.max_prepared_per_domain_per_day:
            reasons.append("Daily outreach preparation cap for this contact domain has been reached")
        domain_cutoff = now - timedelta(hours=policy.domain_cooldown_hours)
        if any(
            self._brief_domain(brief) == domain and brief.created_at >= domain_cutoff
            for brief in product_briefs
        ):
            reasons.append("Contact domain is inside the configured outreach cooling period")
        return reasons

    def _product_briefs(self, product_id: UUID) -> list[OutreachBriefView]:
        briefs: list[OutreachBriefView] = []
        for row in self._store.list_namespace(OUTREACH_BRIEF_NAMESPACE):
            try:
                brief = OutreachBriefView.model_validate(row)
            except ValueError:
                continue
            if brief.product_id == product_id:
                briefs.append(brief)
        return briefs

    def _brief_domain(self, brief: OutreachBriefView) -> str:
        try:
            target = outreach_target_service.get(brief.outreach_target_id)
        except KeyError:
            return ""
        return self._domain(target.business_email)

    def _platform(self, target: OutreachTargetView):
        opportunity = __import__(
            "app.audience_intelligence_service",
            fromlist=["audience_intelligence_service"],
        ).audience_intelligence_service.find_opportunity(target.opportunity_id)
        return opportunity.platform

    def _has_pending_experiment(self, product_id: UUID) -> bool:
        return any(
            experiment.status in {
                DistributionExperimentStatus.DRAFT,
                DistributionExperimentStatus.APPROVED,
            }
            for experiment in distribution_execution_service.list_experiments(product_id)
        )

    def _domain(self, email: str) -> str:
        return email.rsplit("@", 1)[1].casefold()

    def _dedupe(self, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))[:20]


outreach_policy_service = OutreachPolicyService()
outreach_autonomous_preparation_service = OutreachAutonomousPreparationService()
